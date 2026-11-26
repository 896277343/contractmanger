from django.urls import path
from . import views

urlpatterns = [path("", views.dashboard, name="dashboard"), path("logs/", views.update_log, name="update_log"), path("upload/", views.upload, name="upload"), path("import-excel/", views.import_excel, name="import_excel"), path("import-workflows/", views.import_workflows, name="import_workflows"), path("export/", views.export_csv, name="export"), path("login/", views.login_view, name="login"), path("logout/", views.logout_view, name="logout")]
