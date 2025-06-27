from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group

class Command(BaseCommand):
    help = 'Create user groups.'

    def handle(self, *args, **kwargs):
        (group, is_new_group) = Group.objects.get_or_create(name='nations_testers')
