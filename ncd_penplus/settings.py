"""
Django settings for the NCD Clinical and Research Management System.

Environment-based configuration: secrets and deployment values come from
environment variables so the same code runs in development, staging and production.
"""

import os
from pathlib import Path

from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path):
    """Load KEY=VALUE lines from a .env file. Real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_env_file(BASE_DIR / '.env')


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name, default=''):
    return [item.strip() for item in os.environ.get(name, default).split(',') if item.strip()]


SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-n2zl!#b!b4$+p2@==h_p&=v0^jczz8b0esq*#sk!4!*!cw(dw!',
)
DEBUG = env_bool('DJANGO_DEBUG', True)
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')

# Public base URL used in emails (activation / password reset links).
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000')
SITE_NAME = 'NCD Clinical & Research Management System'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
    'users',
    'facilities',
    'audit',
    'patients',
    'clinical',
    'research',
    'encounters',
    'crf',
    'laboratory',
    'pharmacy',
    'integrations',
    'reporting',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Every page requires login unless explicitly marked with @login_not_required.
    'django.contrib.auth.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'ncd_penplus.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site',
            ],
        },
    },
]

WSGI_APPLICATION = 'ncd_penplus.wsgi.application'

# Database is chosen in .env: DB_ENGINE = mysql (default), postgresql or sqlite.
DB_ENGINE = os.environ.get('DB_ENGINE', 'mysql').lower()
if DB_ENGINE == 'sqlite':
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3',
                             'ATOMIC_REQUESTS': True}}
else:
    DATABASES = {
        'default': {
            'ENGINE': {'mysql': 'django.db.backends.mysql',
                       'postgresql': 'django.db.backends.postgresql'}[DB_ENGINE],
            'NAME': os.environ.get('DB_NAME', 'ncd_penplus'),
            'USER': os.environ.get('DB_USER', 'ncd_penplus'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '3306' if DB_ENGINE == 'mysql' else '5432'),
            'ATOMIC_REQUESTS': True,
            'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '60')),
            'TEST': {'NAME': os.environ.get('DB_TEST_NAME', f"test_{os.environ.get('DB_NAME', 'ncd_penplus')}")},
        }
    }
    if DB_ENGINE == 'mysql':
        DATABASES['default']['OPTIONS'] = {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+00:00'",
        }
        DATABASES['default']['TEST'].update({'CHARSET': 'utf8mb4', 'COLLATION': 'utf8mb4_unicode_ci'})

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('DJANGO_TIME_ZONE', 'Africa/Dar_es_Salaam')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Uploaded reports are stored outside the static/public path and served only
# through permission-checked views.
MEDIA_ROOT = Path(os.environ.get('DJANGO_PRIVATE_MEDIA_ROOT', BASE_DIR / 'private_media'))
MEDIA_URL = '/protected-media/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'users.User'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# Activation and password-reset links expire after 3 days.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24 * 3

EMAIL_BACKEND = os.environ.get('DJANGO_EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '25'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', False)
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'NCD System <no-reply@nimr.or.tz>')

# Previous PEN-Plus EDCS (PHP/MySQL) used by `manage.py import_penplus_edcs` (read-only).
LEGACY_EDCS = {
    'host': os.environ.get('EDCS_DB_HOST', 'localhost'),
    'port': int(os.environ.get('EDCS_DB_PORT', '3306')),
    'user': os.environ.get('EDCS_DB_USER', 'root'),
    'password': os.environ.get('EDCS_DB_PASSWORD', ''),
    'database': os.environ.get('EDCS_DB_NAME', 'penplus'),
}

# MySQL cannot create partial ("one open record per patient") unique constraints. Those rules are also
# enforced in model validation and in the services that change them (enrollments, facility transfers).
SILENCED_SYSTEM_CHECKS = ['models.W036'] if DB_ENGINE == 'mysql' else []

MESSAGE_TAGS = {message_constants.ERROR: 'danger'}

SESSION_COOKIE_HTTPONLY = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 60 * 60 * 8
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', True)
    SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', '3600'))
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
