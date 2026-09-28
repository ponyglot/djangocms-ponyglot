SECRET_KEY = "tests"
DEBUG = False
USE_I18N = True
USE_TZ = True
SITE_ID = 1
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("de", "German"), ("fr", "French")]
CMS_LANGUAGES = {
    1: [{"code": code, "name": name} for code, name in LANGUAGES],
    "default": {"fallbacks": [], "hide_untranslated": True, "public": True},
}
CMS_CONFIRM_VERSION4 = True
CMS_TEMPLATES = [("page.html", "Page")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.sites",
    "cms",
    "menus",
    "treebeard",
    "sekizai",
    "djangocms_versioning",
    "djangocms_text",
    "ponyglot",
    "djangocms_ponyglot",
    "tests.testapp",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "cms.middleware.user.CurrentUserMiddleware",
    "cms.middleware.page.CurrentPageMiddleware",
    "cms.middleware.toolbar.ToolbarMiddleware",
    "cms.middleware.language.LanguageCookieMiddleware",
]
ROOT_URLCONF = "tests.urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": ["tests/templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "sekizai.context_processors.sekizai",
                "cms.context_processors.cms_settings",
            ]
        },
    }
]
PONYGLOT = {"API_KEY": "pg_test", "API_URL": "https://api.example.test/v1"}
