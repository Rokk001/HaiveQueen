"""Self-contained, accessible login and first-run setup pages."""
import base64
import html
import json
from pathlib import Path

from . import i18n


def page(setup=False, token='', secure=False, language=None):
    german = i18n.is_german_language(language or i18n.machine_language())
    def t(en, de):
        return html.escape(de if german else en)
    icon = 'data:image/png;base64,' + base64.b64encode(Path(__file__).with_name('web-icon.png').read_bytes()).decode('ascii')
    title = t('Set up your hive', 'Richte deinen Hive ein') if setup else t('Welcome back', 'Willkommen zurück')
    subtitle = t('Create your account and configure access to HaiveQueen.', 'Erstelle deinen Zugang und richte HaiveQueen ein.') if setup else t('Sign in to your HaiveQueen dashboard.', 'Melde dich an deinem HaiveQueen-Dashboard an.')
    button = t('Create account', 'Zugang erstellen') if setup else t('Sign in', 'Anmelden')
    password = 'new-password' if setup else 'current-password'
    fields = (f'<label for="username">{t("Username", "Benutzername")}</label><input id="username" name="username" autocomplete="username" required autofocus>'
              f'<label for="password">{t("Password", "Passwort")}</label><div class="password-field"><input id="password" name="password" type="password" autocomplete="{password}" required>'
              f'<button id="show-password" class="reveal" type="button" aria-controls="password" aria-pressed="false">{t("Show", "Anzeigen")}</button></div>')
    if setup:
        fields += (f'<label for="confirm">{t("Confirm password", "Passwort bestätigen")}</label><input id="confirm" type="password" autocomplete="new-password" required>'
                   f'</fieldset><fieldset><legend><span>02</span> {t("Connection", "Verbindung")}</legend><div class="field-grid"><div><label for="address">{t("Listen address", "Bind-Adresse")}</label>'
                   '<input id="address" name="address" value="0.0.0.0" required spellcheck="false"></div>'
                   f'<div><label for="port">Port</label><input id="port" name="port" type="number" min="1" max="65535" value="8484" required></div></div>'
                   f'<p class="hint">{t("0.0.0.0 allows access from your network. Use 127.0.0.1 for this computer only.", "0.0.0.0 erlaubt den Zugriff im Netzwerk. Mit 127.0.0.1 bleibt der Zugriff auf diesen Computer beschränkt.")}</p>'
                   f'<details><summary>{t("HTTPS certificates", "HTTPS-Zertifikate")} <span>{t("Optional", "Optional")}</span></summary>'
                   f'<p class="hint">{t("Using nginx or another HTTPS reverse proxy? Leave these fields empty. Add certificates only when HaiveQueen serves HTTPS directly.", "Bei nginx oder einem anderen HTTPS-Reverse-Proxy bleiben diese Felder leer. Zertifikate werden nur benötigt, wenn HaiveQueen HTTPS direkt bereitstellt.")}</p>'
                   f'<label for="certificate">{t("Certificate file", "Zertifikatsdatei")}</label><input id="certificate" name="certificate" spellcheck="false">'
                   f'<label for="key">{t("Private key file", "Private Schlüsseldatei")}</label><input id="key" name="key" spellcheck="false"></details>')
        fields = f'<fieldset><legend><span>01</span> {t("Your account", "Dein Zugang")}</legend>' + fields + '</fieldset>'
    connection = (f'<span class="connection secure"><i></i>{t("HTTPS connection", "HTTPS-Verbindung")}</span>' if secure else
                  f'<div class="connection"><i></i><span>{t("HTTP connection — credentials are sent unencrypted. Use HTTPS outside a trusted network.", "HTTP-Verbindung – Zugangsdaten werden unverschlüsselt übertragen. Außerhalb eines vertrauenswürdigen Netzwerks HTTPS verwenden.")}</span></div>')
    bootstrap = json.dumps({'setup': setup, 'token': token, 'busy': 'Wird gespeichert…' if setup and german else 'Saving…' if setup else 'Anmeldung…' if german else 'Signing in…',
                            'show': 'Anzeigen' if german else 'Show', 'hide': 'Verbergen' if german else 'Hide',
                            'mismatch': 'Die Passwörter stimmen nicht überein.' if german else 'Passwords do not match.',
                            'network': 'Keine Verbindung zum Server. Bitte erneut versuchen.' if german else 'Cannot reach the server. Please try again.',
                            'invalid': 'Benutzername oder Passwort ist falsch.' if german else 'Invalid username or password.',
                            'saved': 'Zugang erstellt. Zur Anmeldung' if german else 'Account created. Open login'}).replace('<', '\\u003c')
    css = Path(__file__).with_name('web-access.css').read_text(encoding='utf-8')
    script = Path(__file__).with_name('web-access.js').read_text(encoding='utf-8')
    return (f'<!doctype html><html lang="{"de" if german else "en"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>HaiveQueen {"Setup" if setup else "Login"}</title><link rel="icon" href="{icon}"><style>{css}</style></head>'
            f'<body><div class="topline"><span>HAIVEQUEEN</span><span>{t("FIRST-TIME SETUP", "ERSTEINRICHTUNG") if setup else t("DASHBOARD ACCESS", "DASHBOARD-ZUGANG")}</span></div>'
            f'<main class="access-shell"><section class="identity" aria-label="HaiveQueen"><img class="queen" src="{icon}" alt="">'
            '<div class="wordmark">H<span>ai</span>ve<strong>Queen</strong></div><p class="descriptor">AI AGENT ORCHESTRATOR</p>'
            f'<div class="identity-foot"><span class="hex">⬡</span><p>{t("Plans. Queens. Agents.", "Pläne. Queens. Agents.")}<br><strong>{t("Your hive, in one place.", "Dein Hive. Alles im Blick.")}</strong></p></div></section>'
            f'<section class="access-card"><div class="eyebrow">{"SETUP / 01" if setup else "WELCOME TO THE HIVE"}</div><h1>{title}</h1><p class="subtitle">{subtitle}</p>'
            f'<form id="access-form">{fields}<p id="result" role="alert" aria-live="polite" hidden></p><button class="primary" id="submit" type="submit"><span>{button}</span><span aria-hidden="true">→</span></button></form>'
            f'{connection}<noscript>{t("JavaScript is required to sign in.", "Für die Anmeldung wird JavaScript benötigt.")}</noscript></section></main>'
            f'<script id="access-data" type="application/json">{bootstrap}</script><script>{script}</script></body></html>')
