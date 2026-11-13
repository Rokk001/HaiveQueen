"""Browser shell and shared desktop settings labels; no separate settings store."""
import json
from pathlib import Path

from . import cli_presets, config, gui_config, gui_provider, i18n, projsettings, updates
from . import scheduler as sch


def page(csrf, language=None):
    language = language or i18n.machine_language()
    labels = {**{k: v[0] for k, v in gui_provider.TEXT.items()},
              **gui_config.GENERAL_LABELS, **gui_config.SETTING_LABELS,
              'web': 'Web server', 'certificate': 'Certificate file', 'key': 'Key file',
              'port': 'Port', 'address': 'Address',
              **{k: label for k, label, _ in gui_config.BUILD_FIELDS}}
    help_text = {**gui_config.GENERAL_HELP, **gui_config.SETTING_HELP,
                 **{k: v[1] for k, v in gui_provider.TEXT.items()},
                 **{k: text for k, _, text in gui_config.BUILD_FIELDS}}
    bootstrap = {'csrf': csrf, 'language': language,
                 'translations': i18n.GERMAN if i18n.is_german_language(language) else {},
                 'labels': labels, 'help': help_text, 'choices': gui_config.CHOICES,
                 'fieldLabels': {'General': gui_config.GENERAL_LABELS, 'Projects': gui_config.SETTING_LABELS,
                                 'Providers': {k: v[0] for k, v in gui_provider.TEXT.items()}},
                 'fieldHelp': {'General': gui_config.GENERAL_HELP, 'Projects': gui_config.SETTING_HELP,
                               'Providers': {k: v[1] for k, v in gui_provider.TEXT.items()}},
                 'capabilities': gui_config.CAPABILITIES,
                 'general': {**config.DEFAULT_CONFIG, **{k: sch.DEFAULT_SETTINGS[k] for k in gui_config.GENERAL_LABELS if k in sch.DEFAULT_SETTINGS},
                             'language': 'English',
                             'web': {'address': '0.0.0.0', 'port': 8484, 'certificate': '', 'key': '', 'network': False, 'username': ''}},
                 'project': {k: v for k, v in {**config.DEFAULT_PROJECT, **dict(gui_config.APIARY_DEFAULTS),
                             **dict(gui_config.ADVANCED_DEFAULTS), 'codeRepo': '', 'compileCheck': '',
                             'buildCommand': '', 'deployCommand': '', 'plans': [], 'upstreamRules': True, 'rulesOff': [],
                             'onEvent': {'command': [], 'workDir': '', 'timeoutSeconds': 180}}.items()
                             if k in projsettings.KEPT_KEYS},
                 'presets': cli_presets.PRESETS,
                 'updateConfirm': updates.TEXT_CONFIRM}
    data = json.dumps(bootstrap, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return ('<!doctype html><html lang="' + ('de' if i18n.is_german_language(language) else 'en') + '">'
            '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>HaiveQueen</title><link rel="stylesheet" href="/web-client.css"></head><body>'
            '<header><h1>HaiveQueen <small id="version"></small></h1><div id="controls"></div></header>'
            '<nav id="tabs" aria-label="Navigation"></nav><p id="notice" role="status" aria-live="polite"></p>'
            '<main id="app"></main><aside id="live"></aside><dialog id="detail"><button id="close-detail">×</button>'
            '<div id="detail-body"></div></dialog><script id="bootstrap" type="application/json">' + data +
            '</script><script src="/web-client.js" defer></script></body></html>')


def asset(name):
    if name not in ('web-client.js', 'web-client.css'):
        raise ValueError('Unknown browser asset')
    return Path(__file__).with_name(name).read_text(encoding='utf-8')
