from django.test import TestCase
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from core.api.v1.pagination import MobileCursorPagination
from core.models import Project, Tenant


class MobileCursorPaginationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.projects = [
            Project.objects.create(tenant=self.tenant_a, name=f"Project {index}")
            for index in range(5)
        ]

    def request(self, query="", tenant=None):
        request = Request(self.factory.get(f"/api/v1/projects/{query}"))
        request.tenant = tenant or self.tenant_a
        return request

    def paginate(self, request):
        paginator = MobileCursorPagination()
        page = paginator.paginate_queryset(
            Project.objects.filter(tenant=request.tenant),
            request,
        )
        response = paginator.get_paginated_response([project.pk for project in page])
        return response.data

    def test_forward_paging_uses_stable_order_without_duplicates(self):
        expected = list(
            Project.objects.filter(tenant=self.tenant_a)
            .order_by("-created_at", "-pk")
            .values_list("pk", flat=True)
        )

        first = self.paginate(self.request("?page_size=2"))
        second = self.paginate(
            self.request(f"?page_size=2&cursor={first['pagination']['next_cursor']}")
        )
        third = self.paginate(
            self.request(f"?page_size=2&cursor={second['pagination']['next_cursor']}")
        )

        actual = first["data"] + second["data"] + third["data"]
        self.assertEqual(actual, expected)
        self.assertTrue(first["pagination"]["has_more"])
        self.assertFalse(third["pagination"]["has_more"])

    def test_tampered_cursor_is_rejected(self):
        first = self.paginate(self.request("?page_size=2"))
        cursor = first["pagination"]["next_cursor"]

        with self.assertRaises(NotFound):
            self.paginate(self.request(f"?cursor={cursor}tampered"))

    def test_cursor_cannot_cross_tenant_partition(self):
        first = self.paginate(self.request("?page_size=2"))
        cursor = first["pagination"]["next_cursor"]

        with self.assertRaises(NotFound):
            self.paginate(self.request(f"?cursor={cursor}", tenant=self.tenant_b))

    def test_page_size_is_bounded(self):
        paginator = MobileCursorPagination()

        self.assertEqual(paginator.get_page_size(self.request("?page_size=500")), 100)
        self.assertEqual(paginator.get_page_size(self.request("?page_size=0")), 25)
