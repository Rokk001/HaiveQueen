# usage.ps1 - total token usage of all Claude Code and Codex sessions on this PC, as table data.
# Usage:  . usage.ps1 ; $u = Get-UsageTable -CacheDir <dir> -AgentsFile <f> -ArchiveFile <f> -BrainFile <f>
# Logs are read incrementally (byte offset per file, cache in <CacheDir>\usage-cache.bin).

$script:UsageCs = @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

public class UsageModelAgg
{
    public string Name;
    public bool Codex;
    public long TodayIn, TodayOut, TotalIn, TotalOut;
    public bool HasTotal;
    public long SinceTicks;
}

public class UsageScanResult
{
    public List<UsageModelAgg> Models = new List<UsageModelAgg>();
    public int FilesRead, FilesSeen;
    public long BytesRead;
    public bool CacheWritten;
    public long ClaudeOldestTicks, CodexOldestTicks;
    public int ClaudeFiles, CodexJsonlFiles, CodexOtherFiles;
    public string CodexOldestPath = "";
    public double Seconds;
}

public static class UsageScanner
{
    class ModelInfo { public string Name; public bool Codex; public long MinTicks = long.MaxValue; }
    struct Rec { public ushort Model; public ushort Day; public long In; public long Out; }
    class DayState { public int Day; public long In; public long Out; }
    class FileState
    {
        public long Size, MTime, Offset;
        public int Model = -1;
        public List<DayState> Days = new List<DayState>();
    }
    class State
    {
        public List<ModelInfo> Models = new List<ModelInfo>();
        public Dictionary<string, int> ModelIndex = new Dictionary<string, int>();
        public Dictionary<string, FileState> Claude = new Dictionary<string, FileState>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, FileState> Codex = new Dictionary<string, FileState>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<ulong, Rec> Ids = new Dictionary<ulong, Rec>();
        public bool Dirty;
    }

    static readonly byte[] OutTag = Encoding.ASCII.GetBytes("\"output_tokens\"");
    static readonly byte[] AssistantTag = Encoding.ASCII.GetBytes("\"type\":\"assistant\"");
    static readonly byte[] TurnCtxTag = Encoding.ASCII.GetBytes("\"type\":\"turn_context\"");
    static readonly byte[] EventMsgTag = Encoding.ASCII.GetBytes("\"type\":\"event_msg\"");
    static readonly byte[] TokenCountTag = Encoding.ASCII.GetBytes("\"token_count\"");
    static readonly DateTime DayZero = new DateTime(2020, 1, 1);
    const int CacheVersion = 1;

    // Returns the day number (days since 2020-01-01, local time) of a timestamp text, or -1 if unparsable.
    static int ParseDay(string text, out long ticks)
    {
        ticks = 0;
        DateTimeOffset dto;
        if (!DateTimeOffset.TryParse(text, System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.AssumeUniversal, out dto)) return -1;
        DateTime local = dto.LocalDateTime;
        ticks = local.Ticks;
        return (int)(local.Date - DayZero).TotalDays;
    }

    // Returns the day number of a local date.
    public static int DayOf(DateTime local) { return (int)(local.Date - DayZero).TotalDays; }

    // Normalises a Claude model name (strips date and bracket suffixes); returns null for synthetic or empty names.
    static string NormClaude(string m)
    {
        if (string.IsNullOrEmpty(m)) return null;
        m = Regex.Replace(m, @"\[.*\]$", "");
        m = Regex.Replace(m, @"-\d{8}$", "");
        if (m.Length == 0 || m.IndexOf("synthetic", StringComparison.OrdinalIgnoreCase) >= 0) return null;
        return m;
    }

    // Returns the index of a model in the table, adding it when new.
    static int ModelIdx(State st, string name, bool codex)
    {
        string k = (codex ? "x:" : "a:") + name;
        int i;
        if (st.ModelIndex.TryGetValue(k, out i)) return i;
        ModelInfo mi = new ModelInfo(); mi.Name = name; mi.Codex = codex;
        st.Models.Add(mi); i = st.Models.Count - 1; st.ModelIndex[k] = i; st.Dirty = true;
        return i;
    }

    // FNV-1a 64 bit hash of a message id.
    static ulong Hash(string s)
    {
        ulong h = 14695981039346656037UL;
        for (int i = 0; i < s.Length; i++) { h ^= s[i]; h *= 1099511628211UL; }
        return h;
    }

    // Reads a long property of a JSON object, 0 if missing.
    static long GetLong(JsonElement e, string name)
    {
        JsonElement v;
        if (e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out v) && v.ValueKind == JsonValueKind.Number)
        {
            long r; if (v.TryGetInt64(out r)) return r;
        }
        return 0;
    }

    // Reads complete lines of a file from the offset on and calls the handler per line; returns the offset after the last complete line.
    static long ReadLines(string path, long offset, Action<byte[], int, int> onLine, UsageScanResult res, out long length)
    {
        using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16))
        {
            fs.Seek(offset, SeekOrigin.Begin);
            byte[] buf = new byte[4 << 20];
            int have = 0;
            long pos = offset;
            long total = 0;
            while (true)
            {
                if (have == buf.Length) Array.Resize(ref buf, buf.Length * 2);
                int n = fs.Read(buf, have, buf.Length - have);
                if (n <= 0) break;
                total += n;
                have += n;
                int start = 0;
                while (true)
                {
                    int nl = new ReadOnlySpan<byte>(buf, start, have - start).IndexOf((byte)10);
                    if (nl < 0) break;
                    onLine(buf, start, nl);
                    start += nl + 1;
                }
                if (start > 0)
                {
                    Buffer.BlockCopy(buf, start, buf, 0, have - start);
                    have -= start; pos += start;
                }
            }
            res.BytesRead += total;
            length = fs.Length;
            return pos;
        }
    }

    // Handles one Claude log line: collects usage of assistant messages per message id (highest output wins).
    static void ClaudeLine(byte[] b, int s, int l, State st)
    {
        ReadOnlySpan<byte> span = new ReadOnlySpan<byte>(b, s, l);
        if (span.IndexOf(OutTag) < 0 || span.IndexOf(AssistantTag) < 0) return;
        try
        {
            using (JsonDocument doc = JsonDocument.Parse(new ReadOnlyMemory<byte>(b, s, l)))
            {
                JsonElement root = doc.RootElement, t, msg, id, model, usage, ts;
                if (!root.TryGetProperty("type", out t) || t.GetString() != "assistant") return;
                if (!root.TryGetProperty("message", out msg) || msg.ValueKind != JsonValueKind.Object) return;
                if (!msg.TryGetProperty("id", out id) || id.ValueKind != JsonValueKind.String) return;
                if (!msg.TryGetProperty("model", out model) || model.ValueKind != JsonValueKind.String) return;
                if (!msg.TryGetProperty("usage", out usage) || usage.ValueKind != JsonValueKind.Object) return;
                if (!root.TryGetProperty("timestamp", out ts) || ts.ValueKind != JsonValueKind.String) return;
                string mname = NormClaude(model.GetString());
                if (mname == null) return;
                long ticks;
                int day = ParseDay(ts.GetString(), out ticks);
                if (day < 0) return;
                Rec r = new Rec();
                r.Model = (ushort)ModelIdx(st, mname, false);
                r.Day = (ushort)day;
                r.In = GetLong(usage, "input_tokens") + GetLong(usage, "cache_creation_input_tokens") + GetLong(usage, "cache_read_input_tokens");
                r.Out = GetLong(usage, "output_tokens");
                ulong h = Hash(id.GetString());
                Rec old;
                if (!st.Ids.TryGetValue(h, out old) || r.Out >= old.Out) { st.Ids[h] = r; st.Dirty = true; }
                ModelInfo mi = st.Models[r.Model];
                if (ticks < mi.MinTicks) { mi.MinTicks = ticks; st.Dirty = true; }
            }
        }
        catch (JsonException) { }
    }

    // Handles one Codex log line: tracks the model (turn_context) and cumulative token counts per day.
    static void CodexLine(byte[] b, int s, int l, State st, FileState fs)
    {
        ReadOnlySpan<byte> span = new ReadOnlySpan<byte>(b, s, l);
        ReadOnlySpan<byte> head = span.Slice(0, Math.Min(l, 200));
        try
        {
            if (head.IndexOf(TurnCtxTag) >= 0)
            {
                using (JsonDocument doc = JsonDocument.Parse(new ReadOnlyMemory<byte>(b, s, l)))
                {
                    JsonElement p, m;
                    if (doc.RootElement.TryGetProperty("payload", out p) && p.ValueKind == JsonValueKind.Object
                        && p.TryGetProperty("model", out m) && m.ValueKind == JsonValueKind.String && m.GetString().Length > 0)
                        fs.Model = ModelIdx(st, m.GetString(), true);
                }
                return;
            }
            if (head.IndexOf(EventMsgTag) < 0 || span.IndexOf(TokenCountTag) < 0) return;
            using (JsonDocument doc2 = JsonDocument.Parse(new ReadOnlyMemory<byte>(b, s, l)))
            {
                JsonElement root = doc2.RootElement, p, t, info, tot, ts;
                if (!root.TryGetProperty("payload", out p) || p.ValueKind != JsonValueKind.Object) return;
                if (!p.TryGetProperty("type", out t) || t.GetString() != "token_count") return;
                if (!p.TryGetProperty("info", out info) || info.ValueKind != JsonValueKind.Object) return;
                if (!info.TryGetProperty("total_token_usage", out tot) || tot.ValueKind != JsonValueKind.Object) return;
                if (!root.TryGetProperty("timestamp", out ts) || ts.ValueKind != JsonValueKind.String) return;
                long ticks;
                int day = ParseDay(ts.GetString(), out ticks);
                if (day < 0) return;
                long cin = GetLong(tot, "input_tokens"), cout = GetLong(tot, "output_tokens");
                if (fs.Model < 0) fs.Model = ModelIdx(st, "unbekannt", true);
                DayState last = fs.Days.Count > 0 ? fs.Days[fs.Days.Count - 1] : null;
                if (last != null && last.Day == day) { last.In = cin; last.Out = cout; }
                else if (last == null || last.Day < day)
                {
                    DayState d = new DayState(); d.Day = day; d.In = cin; d.Out = cout; fs.Days.Add(d);
                }
                ModelInfo mi = st.Models[fs.Model];
                if (ticks < mi.MinTicks) mi.MinTicks = ticks;
                st.Dirty = true;
            }
        }
        catch (JsonException) { }
    }

    // Loads the cache file; returns an empty state if it is missing, damaged or of another version.
    static State Load(string path)
    {
        State st = new State();
        if (!File.Exists(path)) return st;
        try
        {
            using (BinaryReader r = new BinaryReader(new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite), Encoding.UTF8))
            {
                if (r.ReadInt32() != CacheVersion) return new State();
                int n = r.ReadInt32();
                for (int i = 0; i < n; i++)
                {
                    ModelInfo mi = new ModelInfo(); mi.Name = r.ReadString(); mi.Codex = r.ReadBoolean(); mi.MinTicks = r.ReadInt64();
                    st.Models.Add(mi); st.ModelIndex[(mi.Codex ? "x:" : "a:") + mi.Name] = i;
                }
                for (int k = 0; k < 2; k++)
                {
                    Dictionary<string, FileState> d = k == 0 ? st.Claude : st.Codex;
                    int fc = r.ReadInt32();
                    for (int i = 0; i < fc; i++)
                    {
                        string p = r.ReadString();
                        FileState fs = new FileState();
                        fs.Size = r.ReadInt64(); fs.MTime = r.ReadInt64(); fs.Offset = r.ReadInt64(); fs.Model = r.ReadInt32();
                        int dc = r.ReadInt32();
                        for (int j = 0; j < dc; j++)
                        {
                            DayState ds = new DayState(); ds.Day = r.ReadInt32(); ds.In = r.ReadInt64(); ds.Out = r.ReadInt64(); fs.Days.Add(ds);
                        }
                        d[p] = fs;
                    }
                }
                int ic = r.ReadInt32();
                for (int i = 0; i < ic; i++)
                {
                    ulong h = r.ReadUInt64(); Rec rec = new Rec();
                    rec.Model = r.ReadUInt16(); rec.Day = r.ReadUInt16(); rec.In = r.ReadInt64(); rec.Out = r.ReadInt64();
                    st.Ids[h] = rec;
                }
            }
        }
        catch (Exception) { return new State(); }
        return st;
    }

    // Writes the cache atomically (temporary file, then move over the old one).
    static void Save(State st, string path)
    {
        string tmp = path + ".tmp";
        using (BinaryWriter w = new BinaryWriter(new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None), Encoding.UTF8))
        {
            w.Write(CacheVersion);
            w.Write(st.Models.Count);
            foreach (ModelInfo mi in st.Models) { w.Write(mi.Name); w.Write(mi.Codex); w.Write(mi.MinTicks); }
            for (int k = 0; k < 2; k++)
            {
                Dictionary<string, FileState> d = k == 0 ? st.Claude : st.Codex;
                w.Write(d.Count);
                foreach (KeyValuePair<string, FileState> kv in d)
                {
                    w.Write(kv.Key); w.Write(kv.Value.Size); w.Write(kv.Value.MTime); w.Write(kv.Value.Offset); w.Write(kv.Value.Model);
                    w.Write(kv.Value.Days.Count);
                    foreach (DayState ds in kv.Value.Days) { w.Write(ds.Day); w.Write(ds.In); w.Write(ds.Out); }
                }
            }
            w.Write(st.Ids.Count);
            foreach (KeyValuePair<ulong, Rec> kv in st.Ids)
            { w.Write(kv.Key); w.Write(kv.Value.Model); w.Write(kv.Value.Day); w.Write(kv.Value.In); w.Write(kv.Value.Out); }
        }
        File.Move(tmp, path, true);
    }

    // Scans new log data (incrementally), updates the cache and returns per-model sums for today and in total.
    public static UsageScanResult Run(string claudeRoot, string codexRoot, string cacheFile, int todayDay)
    {
        System.Diagnostics.Stopwatch sw = System.Diagnostics.Stopwatch.StartNew();
        UsageScanResult res = new UsageScanResult();
        res.ClaudeOldestTicks = long.MaxValue; res.CodexOldestTicks = long.MaxValue;
        State st = Load(cacheFile);
        bool cacheExisted = File.Exists(cacheFile);

        if (Directory.Exists(claudeRoot))
        {
            foreach (FileInfo fi in new DirectoryInfo(claudeRoot).EnumerateFiles("*.jsonl", SearchOption.AllDirectories))
            {
                res.ClaudeFiles++; res.FilesSeen++;
                if (fi.LastWriteTime.Ticks < res.ClaudeOldestTicks) res.ClaudeOldestTicks = fi.LastWriteTime.Ticks;
                FileState fs;
                if (!st.Claude.TryGetValue(fi.FullName, out fs)) { fs = new FileState(); st.Claude[fi.FullName] = fs; st.Dirty = true; }
                long mt = fi.LastWriteTimeUtc.Ticks, len = fi.Length;
                if (fs.Size == len && fs.MTime == mt) continue;
                if (fs.Size == len) { fs.MTime = mt; st.Dirty = true; continue; }
                if (len < fs.Size) fs.Offset = 0;
                try
                {
                    long newLen;
                    fs.Offset = ReadLines(fi.FullName, fs.Offset, delegate (byte[] b, int s, int l) { ClaudeLine(b, s, l, st); }, res, out newLen);
                    fs.Size = newLen; fs.MTime = mt; st.Dirty = true; res.FilesRead++;
                }
                catch (IOException) { }
            }
        }

        if (Directory.Exists(codexRoot))
        {
            foreach (FileInfo fi in new DirectoryInfo(codexRoot).EnumerateFiles("*", SearchOption.AllDirectories))
            {
                if (fi.LastWriteTime.Ticks < res.CodexOldestTicks) { res.CodexOldestTicks = fi.LastWriteTime.Ticks; res.CodexOldestPath = fi.FullName; }
                if (!fi.Name.EndsWith(".jsonl", StringComparison.OrdinalIgnoreCase)) { res.CodexOtherFiles++; continue; }
                res.CodexJsonlFiles++; res.FilesSeen++;
                FileState fs;
                if (!st.Codex.TryGetValue(fi.FullName, out fs)) { fs = new FileState(); st.Codex[fi.FullName] = fs; st.Dirty = true; }
                long mt = fi.LastWriteTimeUtc.Ticks, len = fi.Length;
                if (fs.Size == len && fs.MTime == mt) continue;
                if (fs.Size == len) { fs.MTime = mt; st.Dirty = true; continue; }
                if (len < fs.Size) { fs.Offset = 0; fs.Days.Clear(); fs.Model = -1; }
                try
                {
                    FileState cur = fs;
                    long newLen;
                    fs.Offset = ReadLines(fi.FullName, fs.Offset, delegate (byte[] b, int s, int l) { CodexLine(b, s, l, st, cur); }, res, out newLen);
                    fs.Size = newLen; fs.MTime = mt; st.Dirty = true; res.FilesRead++;
                }
                catch (IOException) { }
            }
        }

        if (st.Dirty || !cacheExisted) { Save(st, cacheFile); res.CacheWritten = true; }

        UsageModelAgg[] agg = new UsageModelAgg[st.Models.Count];
        for (int i = 0; i < agg.Length; i++)
        {
            agg[i] = new UsageModelAgg(); agg[i].Name = st.Models[i].Name; agg[i].Codex = st.Models[i].Codex;
            agg[i].SinceTicks = st.Models[i].MinTicks == long.MaxValue ? 0 : st.Models[i].MinTicks;
        }
        foreach (Rec r in st.Ids.Values)
        {
            UsageModelAgg a = agg[r.Model];
            a.HasTotal = true; a.TotalIn += r.In; a.TotalOut += r.Out;
            if (r.Day == todayDay) { a.TodayIn += r.In; a.TodayOut += r.Out; }
        }
        foreach (FileState fs in st.Codex.Values)
        {
            if (fs.Days.Count == 0 || fs.Model < 0) continue;
            UsageModelAgg a = agg[fs.Model];
            DayState last = fs.Days[fs.Days.Count - 1];
            a.HasTotal = true; a.TotalIn += last.In; a.TotalOut += last.Out;
            if (last.Day == todayDay)
            {
                DayState prev = fs.Days.Count >= 2 ? fs.Days[fs.Days.Count - 2] : null;
                a.TodayIn += last.In - (prev == null ? 0 : prev.In);
                a.TodayOut += last.Out - (prev == null ? 0 : prev.Out);
            }
        }
        foreach (UsageModelAgg a in agg) if (a.HasTotal) res.Models.Add(a);
        sw.Stop(); res.Seconds = sw.Elapsed.TotalSeconds;
        return res;
    }
}
'@

if (-not ('UsageScanner' -as [type])) {
    Add-Type -TypeDefinition $script:UsageCs -Language CSharp -ReferencedAssemblies 'System.Text.Json', 'System.Text.RegularExpressions', 'System.Memory', 'System.Collections', 'System.Runtime', 'System.Diagnostics.Process', 'System.Linq'
}

# Returns the display label for a model key (type plus short name plus version, no effort).
function Get-UsageModelLabel {
    param([string]$Key, [bool]$Codex)
    if ($Codex) {
        if ($Key -match '^gpt-(\d+(?:\.\d+)?)-(.+)$') {
            $name = (($Matches[2] -split '-') | ForEach-Object { if ($_.Length) { $_.Substring(0, 1).ToUpper() + $_.Substring(1) } }) -join ' '
            return "Codex $name $($Matches[1])"
        }
        if ($Key -match '^gpt-(\d+(?:\.\d+)?)$') { return "Codex $($Matches[1])" }
        return "Codex $Key"
    }
    if ($Key -match '^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?$') {
        $v = $Matches[2]; if ($Matches[3]) { $v += '.' + $Matches[3] }
        return 'Claude ' + $Matches[1].Substring(0, [math]::Min(3, $Matches[1].Length)).Substring(0, 1).ToUpper() + $Matches[1].Substring(1, [math]::Min(3, $Matches[1].Length) - 1) + ' ' + $v
    }
    if ($Key -match '^claude-(\d+)(?:-(\d{1,2}))?-([a-z]+)$') {
        $v = $Matches[1]; if ($Matches[2]) { $v += '.' + $Matches[2] }
        $n = $Matches[3]
        return 'Claude ' + $n.Substring(0, 1).ToUpper() + $n.Substring(1, [math]::Min(3, $n.Length) - 1) + ' ' + $v
    }
    if ($Key -like 'claude*') { return "Claude $Key" }
    return $Key
}

# Reads a JSON file that holds an array of objects; returns $null if the file is missing or unreadable.
function Read-UsageJsonArray {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    # The live AGENTS.json is rewritten all the time: read it shared with retries; when it still fails, the last good content is used (never a silently empty list, which made "alle Plaene" drop to the running agent only).
    for ($try = 0; $try -lt 5; $try++) {
        try {
            $fs = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
            try { $txt = (New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)).ReadToEnd() } finally { $fs.Dispose() }
            $rows = @($txt | ConvertFrom-Json)
            $script:UsageJsonLastGood[$Path] = $rows
            return $rows
        } catch { Start-Sleep -Milliseconds 200 }
    }
    if ($script:UsageJsonLastGood.ContainsKey($Path)) { return $script:UsageJsonLastGood[$Path] }
    return $null
}
$script:UsageJsonLastGood = @{}

# Formats a date for the retention texts (fixed day.month.year format).
function Format-UsageDate {
    param([datetime]$Date)
    return $Date.ToString('dd.MM.yyyy', [Globalization.CultureInfo]::InvariantCulture)
}

# Returns the total usage of all Claude Code and Codex sessions as table data (see file header).
function Get-UsageTable {
    param(
        [Parameter(Mandatory)][string]$CacheDir,
        [string]$AgentsFile,
        [string]$ArchiveFile,
        [string]$BrainFile,
        [string]$ClaudeRoot = (Join-Path $env:USERPROFILE '.claude\projects'),
        [string]$CodexRoot = (Join-Path $env:USERPROFILE '.codex\sessions'),
        [string]$PythonExe = ''   # Python 3.14 (module compression.zstd) for the compressed Codex logs; empty = python from the PATH
    )
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    $today = [datetime]::Now.Date
    $scan = [UsageScanner]::Run($ClaudeRoot, $CodexRoot, (Join-Path $CacheDir 'usage-cache.bin'), [UsageScanner]::DayOf($today))

    # Compressed Codex sessions (*.jsonl.zst): a helper (Python 3.14, reads the files only) fills zst-cache.json in the background; the sums found so far are added to the Codex models.
    if ($scan.CodexOtherFiles -gt 0) {
        $zCache = Join-Path $CacheDir 'zst-cache.json'; $zLock = Join-Path $CacheDir 'zst-usage.lock'
        $zBusy = $false
        if (Test-Path -LiteralPath $zLock) { $zp = Get-Content -LiteralPath $zLock -ErrorAction SilentlyContinue; if ($zp -and (Get-Process -Id ([int]$zp) -ErrorAction SilentlyContinue)) { $zBusy = $true } }
        if (-not $zBusy) {
            $py = if ($PythonExe) { $PythonExe } else { [string](Get-Command python -ErrorAction SilentlyContinue).Source }
            if ($py -and (Test-Path -LiteralPath $py)) { Start-Process -FilePath $py -ArgumentList @('-I', (Quote-Arg (Join-Path $PSScriptRoot 'zst-usage.py')), (Quote-Arg $CodexRoot), (Quote-Arg $zCache), (Quote-Arg $zLock)) -WindowStyle Hidden }
        }
        if (Test-Path -LiteralPath $zCache) {
            try {
                $zf = (Get-Content -LiteralPath $zCache -Raw -Encoding UTF8 | ConvertFrom-Json).files
                foreach ($zn in @($zf.PSObject.Properties)) {
                    $ze = $zn.Value
                    $agg = $scan.Models | Where-Object { $_.Codex -and $_.Name -eq $ze.model } | Select-Object -First 1
                    if (-not $agg) { $agg = New-Object UsageModelAgg; $agg.Name = [string]$ze.model; $agg.Codex = $true; $scan.Models.Add($agg) }
                    if ([int64]$ze.in -gt 0 -or [int64]$ze.out -gt 0) { $agg.HasTotal = $true; $agg.TotalIn += [int64]$ze.in; $agg.TotalOut += [int64]$ze.out }
                    if ([int64]$ze.min -gt 0 -and ($agg.SinceTicks -eq 0 -or [int64]$ze.min -lt $agg.SinceTicks)) { $agg.SinceTicks = [int64]$ze.min }
                }
            } catch { }
        }
    }

    # Models from the logs: Claude first, then Codex; inside a group by total input, highest first.
    $byKey = [ordered]@{}
    $sorted = @($scan.Models | Sort-Object @{Expression = { $_.Codex } }, @{Expression = { $_.TotalIn }; Descending = $true })
    foreach ($a in $sorted) {
        $since = $null; if ($a.SinceTicks -gt 0) { $since = [datetime]::new($a.SinceTicks) }
        $byKey[$a.Name] = [pscustomobject]@{
            Key = $a.Name; Label = (Get-UsageModelLabel $a.Name $a.Codex); Codex = $a.Codex
            Today = @{ In = [int64]$a.TodayIn; Out = [int64]$a.TodayOut }
            Plans = @{ In = $null; Out = $null }
            Total = @{ In = [int64]$a.TotalIn; Out = [int64]$a.TotalOut }
            Since = $since; NoData = $false
        }
    }

    # Model that the orchestrator agents of each type run on.
    $claudeKey = ($byKey.Values | Where-Object { -not $_.Codex -and $_.Key -like 'claude-sonnet*' } | Select-Object -First 1).Key
    if (-not $claudeKey) { $claudeKey = 'claude-sonnet-5-5' }
    $codexKey = $null
    $cfg = Join-Path (Split-Path $CodexRoot -Parent) 'config.toml'
    if (Test-Path -LiteralPath $cfg) {
        $m = Select-String -LiteralPath $cfg -Pattern '^\s*model\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) { $codexKey = $m.Matches[0].Groups[1].Value }
    }
    if (-not $codexKey) { $codexKey = ($byKey.Values | Where-Object { $_.Codex } | Select-Object -First 1).Key }

    # Orchestrator sums (plans): agents and archive without pilot and test runs, plus brain calls of Claude Sonnet.
    $sums = @{}
    $add = {
        param($key, $in, $out)
        if (-not $key) { return }
        if (-not $sums.ContainsKey($key)) { $sums[$key] = @{ In = [int64]0; Out = [int64]0 } }
        $sums[$key].In += [int64]$in; $sums[$key].Out += [int64]$out
    }
    $seen = @{}
    foreach ($file in @($AgentsFile, $ArchiveFile)) {
        foreach ($ag in (Read-UsageJsonArray $file)) {
            if ($null -eq $ag.name) { continue }
            if ("$($ag.plan)" -match 'PILOT-PLAN' -or "$($ag.name)" -like 'ZZTEST*') { continue }
            $id = "$($ag.name)|$($ag.startedAt)"
            if ($seen.ContainsKey($id)) { continue }
            $seen[$id] = $true
            $k = if ($ag.type -eq 'codex') { $codexKey } else { $claudeKey }
            & $add $k ($ag.tokensIn) ($ag.tokensOut)
        }
    }
    if ($BrainFile -and (Test-Path -LiteralPath $BrainFile)) {
        foreach ($line in [IO.File]::ReadLines($BrainFile)) {
            if (-not $line.Trim()) { continue }
            try { $br = $line | ConvertFrom-Json } catch { continue }
            if ("$($br.model)" -match 'sonnet') { & $add $claudeKey ($br.input) ($br.output) }
        }
    }
    foreach ($k in $sums.Keys) {
        if (-not $byKey.Contains($k)) {
            $isCodex = ($k -eq $codexKey)
            $byKey[$k] = [pscustomobject]@{
                Key = $k; Label = (Get-UsageModelLabel $k $isCodex); Codex = $isCodex
                Today = @{ In = $null; Out = $null }; Plans = @{ In = $null; Out = $null }
                Total = @{ In = $null; Out = $null }; Since = $null; NoData = $true
            }
        }
        $byKey[$k].Plans = @{ In = $sums[$k].In; Out = $sums[$k].Out }
    }

    # Retention texts with evidence.
    $settings = Join-Path $env:USERPROFILE '.claude\settings.json'
    $days = $null
    if (Test-Path -LiteralPath $settings) {
        try { $days = (Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json).cleanupPeriodDays } catch { }
    }
    $cOld = if ($scan.ClaudeOldestTicks -ne [int64]::MaxValue) { Format-UsageDate ([datetime]::new($scan.ClaudeOldestTicks)) } else { 'keine Datei' }
    if ($null -ne $days) {
        $retClaude = "Claude Code: cleanupPeriodDays = $days in $settings, aelteste Sitzungsdatei vom $cOld."
    } else {
        $retClaude = "Claude Code: kein cleanupPeriodDays in $settings, Standard laut Claude Code 30 Tage (ungeprueft), aelteste Sitzungsdatei vom $cOld."
    }
    $hit = $null
    if (Test-Path -LiteralPath $cfg) { $hit = Select-String -LiteralPath $cfg -Pattern 'retention|cleanup|max_age|expire|history' -List | Select-Object -First 1 }
    $xOld = if ($scan.CodexOldestTicks -ne [int64]::MaxValue) { Format-UsageDate ([datetime]::new($scan.CodexOldestTicks)) } else { 'keine Datei' }
    $cfgText = if ($hit) { "Treffer in ${cfg}: $($hit.Line.Trim())" } else { "keine Aufbewahrungseinstellung in $cfg" }
    $retCodex = "Codex: $cfgText, aelteste Sitzungsdatei vom $xOld ($($scan.CodexOldestPath)); $($scan.CodexOtherFiles) komprimierte Dateien (jsonl.zst) werden nur lesend entpackt und ueber zst-cache.json eingerechnet."

    return [pscustomobject]@{
        Models = @($byKey.Values)
        Retention = "$retClaude $retCodex"
        RetentionClaude = $retClaude
        RetentionCodex = $retCodex
        Stats = [pscustomobject]@{
            Seconds = $scan.Seconds; FilesSeen = $scan.FilesSeen; FilesRead = $scan.FilesRead; BytesRead = $scan.BytesRead
            CacheWritten = $scan.CacheWritten; ClaudeFiles = $scan.ClaudeFiles; CodexJsonlFiles = $scan.CodexJsonlFiles
            CodexSkippedFiles = $scan.CodexOtherFiles
        }
    }
}
