"""Browser shell and shared settings labels; no separate settings store."""
import base64
import json
from pathlib import Path

from . import cli_presets, config, settings, provider_labels, i18n, projsettings, updates
from . import scheduler as sch


def page(csrf, language=None):
    language = language or i18n.machine_language()
    labels = {**{k: v[0] for k, v in provider_labels.TEXT.items()},
              **settings.GENERAL_LABELS, **settings.SETTING_LABELS,
              'web': 'Web server', 'certificate': 'Certificate file', 'key': 'Key file',
              'port': 'Port', 'address': 'Address',
              **{k: label for k, label, _ in settings.BUILD_FIELDS}}
    help_text = {**settings.GENERAL_HELP, **settings.SETTING_HELP,
                 **{k: v[1] for k, v in provider_labels.TEXT.items()},
                 **{k: text for k, _, text in settings.BUILD_FIELDS}}
    bootstrap = {'csrf': csrf, 'language': language,
                 'translations': i18n.GERMAN if i18n.is_german_language(language) else {},
                 'labels': labels, 'help': help_text, 'choices': settings.CHOICES,
                 'fieldLabels': {'General': settings.GENERAL_LABELS, 'Projects': settings.SETTING_LABELS,
                                 'Providers': {k: v[0] for k, v in provider_labels.TEXT.items()}},
                 'fieldHelp': {'General': settings.GENERAL_HELP, 'Projects': settings.SETTING_HELP,
                               'Providers': {k: v[1] for k, v in provider_labels.TEXT.items()}},
                 'capabilities': settings.CAPABILITIES,
                 'choiceLabels': {'access': dict(provider_labels.ACCESS)},
                 'general': {**config.DEFAULT_CONFIG, **{k: sch.DEFAULT_SETTINGS[k] for k in settings.GENERAL_LABELS if k in sch.DEFAULT_SETTINGS},
                             'language': 'English',
                             'web': {'address': '0.0.0.0', 'port': 8484, 'certificate': '', 'key': '', 'network': False, 'username': ''}},
                 'project': {k: v for k, v in {**config.DEFAULT_PROJECT, **dict(settings.APIARY_DEFAULTS),
                             **dict(settings.ADVANCED_DEFAULTS), 'codeRepo': '', 'compileCheck': '',
                             'buildCommand': '', 'deployCommand': '', 'plans': [], 'upstreamRules': True, 'rulesOff': [],
                             'onEvent': {'command': [], 'workDir': '', 'timeoutSeconds': 180}}.items()
                             if k in projsettings.KEPT_KEYS},
                 'presets': cli_presets.PRESETS,
                 'accessClis': {access: cli_presets.keys_for_access(access) for access in ('subscription', 'api', 'llm')},
                 'cliModels': {key: {'models': cli_presets.model_list(key), 'efforts': cli_presets.effort_list(key)} for key in cli_presets.PRESETS},
                 'updateConfirm': updates.TEXT_CONFIRM}
    favicon = 'data:image/png;base64,' + base64.b64encode(Path(__file__).with_name('web-icon.png').read_bytes()).decode('ascii')
    banner = Path(__file__).with_name('web-header.png')
    branding = ('<img class="brand-banner" alt="HaiveQueen — AI Agent Orchestrator" src="data:image/png;base64,'
                + base64.b64encode(banner.read_bytes()).decode('ascii') + '">') if banner.is_file() else '<h1>HaiveQueen</h1>'
    data = json.dumps(bootstrap, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return ('<!doctype html><html lang="' + ('de' if i18n.is_german_language(language) else 'en') + '">'
            '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>HaiveQueen</title><link rel="icon" type="image/png" href="' + favicon + '"><link rel="stylesheet" href="/web-client.css"></head><body>'
            '<header class="app-header"><div class="environment-bar"><span id="environment-label">HaiveQueen</span><div class="version-group"><span id="version"></span><span id="update-status"></span></div></div><div class="brand-header"><canvas id="honeycomb" aria-hidden="true"></canvas>' + branding + '</div><div class="command-bar"><div id="controls"></div></div>'
            '<nav id="tabs" aria-label="Navigation"></nav></header><p id="notice" role="status" aria-live="polite"></p>'
            '<main id="app"></main><aside id="live"></aside><dialog id="detail" aria-labelledby="detail-title"><button id="close-detail" aria-label="Close">×</button>'
            '<div id="detail-body"></div></dialog><script id="bootstrap" type="application/json">' + data +
            '</script><script src="/web-client.js" defer></script></body></html>')


def asset(name):
    if name not in ('web-client.js', 'web-client.css'):
        raise ValueError('Unknown browser asset')
    return Path(__file__).with_name(name).read_text(encoding='utf-8')
