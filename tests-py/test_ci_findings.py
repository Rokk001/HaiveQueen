"""Regression coverage for CI discovery and browser rerender synchronization."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path
import isolation

ROOT = Path(__file__).resolve().parent.parent

class CiFindingsTests(unittest.TestCase):
    def test_workflow_discovery_patterns_have_tests(self):
        source = (ROOT / '.github/workflows/test.yml').read_text(encoding='utf-8')
        for pattern in re.findall(r'-p (test[^ \n]+)', source):
            self.assertTrue(list((ROOT / 'tests-py').glob(pattern)), pattern)

    @unittest.skipUnless(shutil.which('node'), 'Node is required')
    def test_browser_click_accepts_replaced_button(self):
        source = (ROOT / 'tests-py/web_browser_test.js').read_text(encoding='utf-8')
        helpers = source[source.index('  const wait ='):source.index('  const field =')]
        script = """
        let attached=true;
        const node={disabled:false,isConnected:true,textContent:'Save',dataset:{},click(){this.disabled=true;this.isConnected=false;attached=false;}};
        const document={querySelectorAll:()=>attached?[node]:[],getElementById:()=>null};
        const setTimeout=fn=>fn();
        """ + helpers + "\nclick('Save').catch(error=>{console.error(error.message);process.exitCode=1});"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_compile_milestone_accepts_regional_time_with_spaces(self):
        from haivequeen import integration
        match=integration.COMPILE_MILESTONE.match('Green\t9:12 AM Compile test - Exit 0')
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1),'Green')
