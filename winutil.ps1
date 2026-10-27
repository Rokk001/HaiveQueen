Add-Type @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class WinUtil {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static List<string> List(string procName) {
    var r = new List<string>();
    EnumWindows((h,l)=>{ uint pid; GetWindowThreadProcessId(h,out pid);
      try { var p = System.Diagnostics.Process.GetProcessById((int)pid);
        if(p.ProcessName==procName && IsWindowVisible(h)){ var sb=new StringBuilder(256); GetWindowText(h,sb,256); r.Add(h.ToInt64()+"|"+sb); } } catch {}
      return true; }, IntPtr.Zero);
    return r; }
  public static void Front(long h){ IntPtr p=new IntPtr(h); ShowWindow(p,3); SetWindowPos(p,new IntPtr(-1),0,0,0,0,0x0003); SetWindowPos(p,new IntPtr(-2),0,0,0,0,0x0003); }
  public static void Close(long h){ PostMessage(new IntPtr(h),0x0010,IntPtr.Zero,IntPtr.Zero); }
}
"@

