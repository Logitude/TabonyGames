# Isolated tests need neither production secrets nor the game engine/static assets.
SECRET_KEY = 'turn-feed-tests-only'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes',
                  'users.apps.UsersConfig', 'Nations.apps.NationsConfig']
AUTH_USER_MODEL = 'users.User'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
NATIONS_TURN_FEED_ENABLED = True
