from django.shortcuts import render

def credentials(request):
    return render(request, 'users/credentials.html')
