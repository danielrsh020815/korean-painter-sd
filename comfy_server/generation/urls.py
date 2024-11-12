from django.urls import path

from .views import FetchImage, GetProgress, GetWorkflows, QueuePrompt

urlpatterns = [
    path('workflows/', GetWorkflows.as_view(), name='get_workflow'),
    path('prompt/', QueuePrompt.as_view(), name='prompt'),
    path('fetch/', FetchImage.as_view(), name='fetch_image'),
    path('progress/', GetProgress.as_view(), name='get_progress'),
]
