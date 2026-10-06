from django.http import HttpResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from api.mixins import CompanyScopedMixin
from api.serializers.imports import ImportBatchSerializer, UploadSerializer
from importer.commit import commit_batch, undo_batch
from importer.errors import ImportFileError, ImportStateError, MappingError, StaleDryRun, UndoBlocked
from importer.mapping import (
    FIELDS, distinct_status_values, suggest_mapping, validate_mapping, validate_status_mapping,
)
from importer.parsers import MAX_BYTES, parse_upload
from importer.reports import issues_csv
from importer.validation import validate_import
from tracker.models import ImportBatch, ObligationType

EDITABLE = (ImportBatch.Status.UPLOADED, ImportBatch.Status.MAPPED, ImportBatch.Status.VALIDATED)
ISSUES_PREVIEW = 100


class ImportBatchViewSet(CompanyScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Spreadsheet import: upload -> mapping -> status mapping -> dry run -> commit (-> undo). MD and Manager only."""
    queryset = ImportBatch.objects.select_related("created_by").defer("rows")
    serializer_class = ImportBatchSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "head", "options"]
    required_actions = {name: "import_data" for name in (
        "list", "retrieve", "create", "mapping", "status_mapping", "dry_run", "errors_csv", "commit", "undo")}

    # ---- helpers ----

    def _type_names(self, request):
        return set(ObligationType.objects.filter(company_id=request.user.company_id, is_active=True)
                   .values_list("name", flat=True))

    def _editable(self):
        batch = self.get_object()
        if batch.status not in EDITABLE:
            raise ImportStateError(f"This import is already {batch.get_status_display().lower()} and can no longer be changed.")
        return batch

    @staticmethod
    def _bad(message, status=400, **extra):
        return Response({"detail": message, **extra}, status=status)

    # ---- steps ----

    def create(self, request, *args, **kwargs):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        if upload.size > MAX_BYTES:
            return self._bad(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        try:
            parsed = parse_upload(upload.name, upload.read(),
                                  sheet=serializer.validated_data.get("sheet") or None,
                                  header_row=serializer.validated_data.get("header_row"))
        except ImportFileError as exc:
            return self._bad(str(exc))
        type_names = sorted(self._type_names(request))
        batch = ImportBatch.objects.create(
            company=request.user.company, created_by=request.user, filename=upload.name[:255],
            sheet_name=parsed.sheet, sheets=parsed.sheets, file_sha256=parsed.sha256,
            headers=parsed.headers, rows=parsed.rows, row_count=len(parsed.rows))
        data = ImportBatchSerializer(batch).data
        data.update({
            "header_row": parsed.header_row,
            "suggested_mapping": suggest_mapping(parsed.headers, type_names),
            "fields": [{"key": k, "label": label, "required": req} for k, label, req in FIELDS],
            "obligation_types": type_names,
            "sample_rows": [r["v"] for r in parsed.rows[:5]],
        })
        return Response(data, status=201)

    @action(detail=True, methods=["post"])
    def mapping(self, request, pk=None):
        try:
            batch = self._editable()
            cleaned = validate_mapping(request.data.get("mapping"), batch.headers, self._type_names(request))
        except ImportStateError as exc:
            return self._bad(str(exc))
        except MappingError as exc:
            return Response({"detail": "The column mapping has problems.", "mapping": exc.messages}, status=400)
        batch.mapping, batch.report, batch.status = cleaned, {}, ImportBatch.Status.MAPPED
        batch.save(update_fields=["mapping", "report", "status"])
        chosen = batch.status_mapping or {}
        values = [{**v, "chosen": chosen.get(v["key"])} for v in distinct_status_values(batch)]
        return Response({"mapping": cleaned, "status_values": values})

    @action(detail=True, methods=["post"], url_path="status-mapping", url_name="status-mapping")
    def status_mapping(self, request, pk=None):
        try:
            batch = self._editable()
            if not batch.mapping:
                return self._bad("Map the columns first.")
            cleaned = validate_status_mapping(request.data.get("status_mapping"))
        except ImportStateError as exc:
            return self._bad(str(exc))
        except MappingError as exc:
            return Response({"detail": "The status mapping has problems.", "status_mapping": exc.messages}, status=400)
        batch.status_mapping = {**(batch.status_mapping or {}), **cleaned}
        batch.report, batch.status = {}, ImportBatch.Status.MAPPED
        batch.save(update_fields=["status_mapping", "report", "status"])
        return Response({"status_mapping": batch.status_mapping})

    @action(detail=True, methods=["post"], url_path="dry-run", url_name="dry-run")
    def dry_run(self, request, pk=None):
        try:
            batch = self._editable()
        except ImportStateError as exc:
            return self._bad(str(exc))
        if not batch.mapping:
            return self._bad("Map the columns first.")
        report, _ = validate_import(batch)
        batch.report = report
        batch.status = ImportBatch.Status.MAPPED if report["mapping_problems"] else ImportBatch.Status.VALIDATED
        batch.save(update_fields=["report", "status"])
        return Response({
            "status": batch.status, "summary": report["summary"], "unmapped_statuses": report["unmapped_statuses"],
            "mapping_problems": report["mapping_problems"], "issues": report["issues"][:ISSUES_PREVIEW],
            "issues_total": len(report["issues"]), "issues_truncated": report["issues_truncated"],
        })

    @action(detail=True, methods=["get"], url_path=r"errors\.csv", url_name="errors-csv")
    def errors_csv(self, request, pk=None):
        batch = self.get_object()
        if not batch.report:
            return self._bad("Run a dry run first.")
        response = HttpResponse(issues_csv(batch), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="import-{batch.pk}-issues.csv"'
        return response

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        if request.data.get("confirm") is not True:
            return self._bad('Send {"confirm": true} to import.')
        batch = self.get_object()
        try:
            counts = commit_batch(batch, request.user)
        except ImportStateError as exc:
            return self._bad(str(exc))
        except StaleDryRun as exc:
            return self._bad(str(exc), status=409)
        return Response({"status": ImportBatch.Status.COMMITTED, "counts": counts})

    @action(detail=True, methods=["post"])
    def undo(self, request, pk=None):
        batch = self.get_object()
        try:
            undo_batch(batch, request.user)
        except ImportStateError as exc:
            return self._bad(str(exc))
        except UndoBlocked as exc:
            return self._bad("This import cannot be undone.", status=409, reasons=exc.reasons)
        return Response({"status": ImportBatch.Status.UNDONE})
