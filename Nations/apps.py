from django.apps import AppConfig

class NationsConfig(AppConfig):
    name = 'Nations'

    def ready(self):
        from . import turn_feed_signals
