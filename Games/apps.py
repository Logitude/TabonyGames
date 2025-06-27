from django.conf import settings
from django.apps import AppConfig

class GamesConfig(AppConfig):
    name = 'Games'

    def ready(self):
        if settings.USE_AMAZON_SES:
            import boto3
            self.aws_email_client = boto3.client('ses', aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY, region_name=settings.AWS_REGION)
