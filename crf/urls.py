from django.urls import path

from . import views

app_name = 'crf'

urlpatterns = [
    path('forms/', views.DefinitionListView.as_view(), name='definition_list'),
    path('forms/new/', views.DefinitionCreateView.as_view(), name='definition_create'),
    path('forms/<int:pk>/', views.DefinitionDetailView.as_view(), name='definition_detail'),
    path('forms/<int:pk>/edit/', views.DefinitionUpdateView.as_view(), name='definition_update'),
    path('versions/<int:pk>/', views.VersionDetailView.as_view(), name='version_detail'),
    path('versions/<int:pk>/publish/', views.VersionPublishView.as_view(), name='version_publish'),
    path('versions/<int:pk>/new-version/', views.VersionCloneView.as_view(), name='version_clone'),
    path('versions/<int:version_pk>/sections/add/', views.SectionCreateView.as_view(), name='section_add'),
    path('versions/<int:version_pk>/sections/<int:pk>/edit/', views.SectionUpdateView.as_view(), name='section_update'),
    path('versions/<int:version_pk>/fields/add/', views.FieldCreateView.as_view(), name='field_add'),
    path('versions/<int:version_pk>/fields/<int:pk>/edit/', views.FieldUpdateView.as_view(), name='field_update'),
    path('versions/<int:version_pk>/<str:kind>/<int:pk>/delete/', views.BuilderDeleteView.as_view(), name='builder_delete'),
    path('submissions/', views.SubmissionListView.as_view(), name='submission_list'),
    path('patients/<int:patient_pk>/fill/<int:version_pk>/', views.SubmissionFormView.as_view(), name='submission_add'),
    path('submissions/<int:pk>/', views.SubmissionDetailView.as_view(), name='submission_detail'),
    path('submissions/<int:pk>/edit/', views.SubmissionFormView.as_view(), name='submission_update'),
    path('submissions/<int:pk>/review/', views.SubmissionReviewView.as_view(), name='submission_review'),
    path('encounters/<int:encounter_pk>/not-applicable/<int:version_pk>/', views.SubmissionNotApplicableView.as_view(),
         name='submission_not_applicable'),
]
