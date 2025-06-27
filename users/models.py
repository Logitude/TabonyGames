from django.db import models
from django.core.validators import MinLengthValidator, MaxLengthValidator, RegexValidator
from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.auth.decorators import user_passes_test

class UserManager(UserManager):
    def get_by_natural_key(self, username):
        return self.get(**{f'{self.model.USERNAME_FIELD}__iexact': username})

class User(AbstractUser):
    username = models.CharField(
        'username',
        max_length=20,
        unique=True,
        help_text='Required. 3-20 characters. Letters, numbers, and _ only.',
        validators=[MinLengthValidator(3), MaxLengthValidator(20), RegexValidator(regex=r'^\w+$')],
        error_messages={
            'unique': 'A user with that username already exists.',
        },
    )
    email = models.EmailField()
    turn_notification_emails = models.BooleanField('Enable turn notification emails?', default=False)
    objects = UserManager()

def get_deleted_user():
    return User.objects.get_or_create(username='deleted_player', email='deleted_player@games.tabony.net')[0]

def get_deleted_user_id():
    return get_deleted_user().id

def is_superuser_test(user):
    return user.is_superuser

is_superuser = user_passes_test(is_superuser_test, login_url='/accounts/profile/')

def is_nations_tester_test(user):
    return user.is_superuser or user.groups.filter(name='nations_testers').exists()

is_nations_tester = user_passes_test(is_nations_tester_test, login_url='/accounts/profile/')
