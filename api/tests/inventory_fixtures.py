"""Shared fixtures for the inventory API tests (not a test module itself)."""
from datetime import date

from rest_framework.test import APITestCase

from api.permissions import ALL_ROLES
from tracker import inventory_services as inv
from tracker import services
from tracker.models import Buyer, Company, User


class InventoryApiBase(APITestCase):
    """Two companies. Company A has one user per role; company B has an MD and an estate of its own."""

    def setUp(self):
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.users = {role: User.objects.create_user(f"{role}_a", password="x", company=self.a, role=role)
                      for role in sorted(ALL_ROLES)}
        self.md, self.manager = self.users["md"], self.users["manager"]
        self.user_b = User.objects.create_user("md_b", password="x", company=self.b, role="md")
        self.no_company = User.objects.create_user("lonely", password="x", role="md")
        self.estate = inv.create_estate(self.a, self.md, name="Green Valley", location="Lekki")
        self.estate_b = inv.create_estate(self.b, self.user_b, name="Palm Heights")
        self.buyer = Buyer.objects.create(company=self.a, full_name="Ada Obi", phone="0801", email="ada@x.test")

    def login(self, role="md"):
        self.client.force_authenticate(self.users[role])

    def plot(self, number, estate=None, company=None, actor=None):
        return inv.register_plot(company or self.a, actor or self.md, estate=estate or self.estate, plot_number=number)

    def sell(self, plot, price=1000, paid=0, buyer=None):
        return services.create_sale(self.a, plot, buyer or self.buyer, price, date(2026, 1, 5), paid, actor=self.md)
