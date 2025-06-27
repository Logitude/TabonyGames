from django import forms
from users.models import User
from django.contrib.auth.forms import UserCreationForm

class UserCreationFormWithEmail(UserCreationForm):
    email = forms.EmailField(required=True, label='Email', help_text='Required.')
    accept_cookies = forms.BooleanField(required=True, label='I agree to store cookies from this site on my devices in order to retain my login.', help_text='Required.', widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def clean_username(self):
        username = self.cleaned_data['username']
        if username and User.objects.filter(username__iexact=username).exists():
            self.add_error('username', 'User with this Username already exists.')
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user

class ProfileSettings(forms.ModelForm):
    turn_notification_emails = forms.BooleanField(required=False, label='Enable turn notification emails?', widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    class Meta:
        model = User
        fields = ('turn_notification_emails',)
