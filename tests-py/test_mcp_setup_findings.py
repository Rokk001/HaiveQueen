"""Capability setup is confirmed, profile-scoped and verified against advertised tools."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import isolation
from haivequeen import profile, scheduler

class McpSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        patch=mock.patch.object(profile,'profile_dir',return_value=Path(self.temp.name));patch.start();self.addCleanup(patch.stop)
        self.provider={'name':'example','protocol':'claude','command':'claude','capabilitiesVersion':1,'capabilities':['Coding','3D modelling']}
        self.setup={'mcpServers':{'bridge':{'command':'bridge','args':[]}},'capabilities':{'3D modelling':{'server':'bridge','tools':['scene_read']}}}

    def test_checkbox_alone_does_not_enable_external_capability(self):
        self.assertNotIn('3D modelling',scheduler.provider_capabilities(self.provider))
        self.assertIn('Coding',scheduler.provider_capabilities(self.provider))

    def test_confirmation_and_tool_proof_are_required(self):
        from haivequeen import mcp_setup
        with mock.patch.object(mcp_setup,'probe',return_value=['scene_read']) as probe:
            with self.assertRaises(ValueError):mcp_setup.configure(self.provider,self.setup,confirmed=False)
            probe.assert_not_called()
            state=mcp_setup.configure(self.provider,self.setup,confirmed=True)
        self.assertIn('3D modelling',state['available'])
        self.assertIn('3D modelling',scheduler.provider_capabilities(self.provider))
        args=mcp_setup.arguments(self.provider)
        self.assertIn('--mcp-config',args)
        file=Path(args[args.index('--mcp-config')+1])
        self.assertTrue(file.is_relative_to(profile.provider_home(self.provider)))
        self.assertEqual(json.loads(file.read_text())['mcpServers'],self.setup['mcpServers'])
        profile.prepare(self.provider)
        self.assertTrue(file.exists())

    def test_failed_recheck_revokes_proof_without_secrets_in_status(self):
        from haivequeen import mcp_setup
        with mock.patch.object(mcp_setup,'probe',return_value=['scene_read']):mcp_setup.configure(self.provider,self.setup,confirmed=True)
        with mock.patch.object(mcp_setup,'probe',side_effect=ValueError('private-token')):
            state=mcp_setup.configure(self.provider,self.setup,confirmed=True)
        self.assertNotIn('3D modelling',state['available'])
        self.assertNotIn('private-token',json.dumps(state))
        self.assertNotIn('3D modelling',scheduler.provider_capabilities(self.provider))

    def test_missing_tool_never_enables_capability(self):
        from haivequeen import mcp_setup
        with mock.patch.object(mcp_setup,'probe',return_value=['other']):
            state=mcp_setup.configure(self.provider,self.setup,confirmed=True)
        self.assertEqual(state['available'],[])

    def test_codex_override_is_valid_toml(self):
        import tomllib
        from haivequeen import mcp_setup
        provider={**self.provider,'protocol':'codex'}
        with mock.patch.object(mcp_setup,'probe',return_value=['scene_read']):mcp_setup.configure(provider,self.setup,confirmed=True)
        args=mcp_setup.arguments(provider)
        parsed=tomllib.loads(args[1])
        self.assertEqual(parsed['mcp_servers']['bridge']['command'],'bridge')

    def test_rejects_unknown_fields_instead_of_losing_configuration(self):
        from haivequeen import mcp_setup
        self.setup['mcpServers']['bridge']['unknownOption']=True
        with self.assertRaises(ValueError):mcp_setup.configure(self.provider,self.setup,confirmed=True)

    def test_real_stdio_handshake_and_paginated_tools(self):
        import sys
        from haivequeen import mcp_setup, proctree
        script=Path(self.temp.name)/'server.py'
        script.write_text("import sys,json\nfor line in sys.stdin:\n m=json.loads(line)\n if 'id' not in m: continue\n r={'protocolVersion':'2025-06-18','capabilities':{'tools':{}}} if m['method']=='initialize' else ({'tools':[{'name':'other'}],'nextCursor':'next'} if not m.get('params',{}).get('cursor') else {'tools':[{'name':'scene_read'}]})\n print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)\n",encoding='utf-8')
        profile.provider_home(self.provider).mkdir(parents=True,exist_ok=True)
        with mock.patch.object(proctree,'Tracker'):
            result=mcp_setup.probe({'command':sys.executable,'args':[str(script)]},self.provider)
        self.assertEqual(result,['other','scene_read'])

    def test_opencode_profile_preserves_existing_provider_config(self):
        from haivequeen import mcp_setup
        provider={**self.provider,'protocol':'opencode'}
        with mock.patch.object(mcp_setup,'probe',return_value=['scene_read']), mock.patch('subprocess.run',return_value=mock.Mock(returncode=0,stdout='2.0.0')):
            mcp_setup.configure(provider,self.setup,confirmed=True)
        env=mcp_setup.environment(provider,{'OPENCODE_CONFIG_CONTENT':'{"provider":{"local":{}}}'})
        content=json.loads(env['OPENCODE_CONFIG_CONTENT'])
        self.assertIn('local',content['provider'])
        self.assertEqual(content['mcp']['servers']['bridge']['command'],['bridge'])
