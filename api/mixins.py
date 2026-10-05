class CompanyScopedMixin:
    """Put FIRST in the class bases. Every query is limited to the caller's company, so another
    company's object simply does not exist for this user (404, not 403)."""

    def get_queryset(self):
        return super().get_queryset().filter(company_id=self.request.user.company_id)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)
