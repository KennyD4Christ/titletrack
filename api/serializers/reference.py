from rest_framework import serializers

from tracker.models import Buyer, Department, Estate, Plot, Sale, User


class EstateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Estate
        fields = ["id", "name", "location"]


class PlotSerializer(serializers.ModelSerializer):
    estate_name = serializers.CharField(source="estate.name", read_only=True)

    class Meta:
        model = Plot
        fields = ["id", "estate", "estate_name", "plot_number"]


class BuyerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Buyer
        fields = ["id", "full_name", "phone", "email"]


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name"]


class UserLiteSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "role", "department"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class SaleSerializer(serializers.ModelSerializer):
    is_fully_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Sale
        fields = ["id", "plot", "buyer", "price", "amount_paid", "sale_date",
                  "fully_paid_on", "status", "is_fully_paid"]
