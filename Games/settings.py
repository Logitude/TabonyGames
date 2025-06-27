from os import getenv
from pathlib import Path
from configparser import ConfigParser

IN_PRODUCTION = getenv('DJANGO_LOCAL_RUN', 'FALSE') != 'TRUE'

BASE_DIR = Path(__file__).resolve().parent.parent

if IN_PRODUCTION:
    secrets_path = BASE_DIR.parent / 'secrets.ini'
else:
    secrets_path = BASE_DIR / 'local' / 'secrets.ini'

with open(secrets_path) as secrets_file:
    secrets = ConfigParser(interpolation=None)
    secrets.read_file(secrets_file)
    SECRET_KEY = secrets['django']['secret_key']
    db_password = secrets['django']['db_password']
    HELP_EMAIL = secrets['django']['help_email']
    DEFAULT_FROM_EMAIL = secrets['django']['from_email']
    AWS_ACCESS_KEY_ID = secrets['aws']['aws_access_key_id']
    AWS_SECRET_ACCESS_KEY = secrets['aws']['aws_secret_access_key']
    AWS_SMTP_PASSWORD = secrets['aws']['aws_smtp_password']
    AWS_REGION = secrets['aws']['region']

DEBUG = not IN_PRODUCTION

if IN_PRODUCTION:
    ALLOWED_HOSTS = ['games.tabony.net', '208.113.131.91']
else:
    ALLOWED_HOSTS = ['127.0.0.1', '192.168.1.6']

CSRF_COOKIE_SECURE = IN_PRODUCTION
SESSION_COOKIE_SECURE = IN_PRODUCTION
SESSION_COOKIE_AGE = 31536000

INSTALLED_APPS = [
    'users.apps.UsersConfig',
    'Games.apps.GamesConfig',
    'Nations.apps.NationsConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'channels',
    'widget_tweaks',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'Games.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates'
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'Games.wsgi.application'
ASGI_APPLICATION = 'Games.asgi.application'

if IN_PRODUCTION:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
        }
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        }
    }

MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

SITE_ID = 1

USE_AMAZON_SES = IN_PRODUCTION

if USE_AMAZON_SES:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = 'email-smtp.us-east-2.amazonaws.com'
    EMAIL_PORT = 587
    EMAIL_HOST_USER = AWS_ACCESS_KEY_ID
    EMAIL_HOST_PASSWORD = AWS_SMTP_PASSWORD
    EMAIL_USE_TLS = True
    EMAIL_TIMEOUT = 6.0

if IN_PRODUCTION:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'tabonygames',
            'USER': 'tabony_games',
            'PASSWORD': db_password,
            'HOST': 'localhost',
            'PORT': '5435',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'local' / 'db.sqlite3',
        }
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'

USE_I18N = False

USE_L10N = False

USE_TZ = True

STATIC_URL = '/static/'
if IN_PRODUCTION:
    STATIC_ROOT = BASE_DIR.parent.parent.parent.parent / 'public' / 'static'
else:
    STATIC_ROOT = BASE_DIR / 'static'
    STATICFILES_DIRS = [
        BASE_DIR / 'Games' / 'static',
    ]

LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'home'

if IN_PRODUCTION:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'handlers': {
            'file': {
                'level': 'WARNING',
                'class': 'logging.FileHandler',
                'filename': BASE_DIR.parent.parent.parent.parent / 'logs' / 'django.log',
            },
        },
        'loggers': {
            'django': {
                'handlers': ['file'],
                'level': 'WARNING',
                'propagate': True,
            },
        },
    }
