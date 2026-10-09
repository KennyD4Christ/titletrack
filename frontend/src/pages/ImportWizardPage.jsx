import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import PageHeader from "../components/layout/PageHeader.jsx";
import ImportStepper from "../components/imports/ImportStepper.jsx";
import UploadStep from "../components/imports/UploadStep.jsx";
import ColumnMappingStep from "../components/imports/ColumnMappingStep.jsx";
import StatusMappingStep from "../components/imports/StatusMappingStep.jsx";
import DryRunStep from "../components/imports/DryRunStep.jsx";
import DoneStep from "../components/imports/DoneStep.jsx";
import { uploadImport } from "../api/imports.js";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";

export default function ImportWizardPage() {
  const [step, setStep] = useState("upload");
  const [file, setFile] = useState(null);
  const [upload, setUpload] = useState(null);
  const [statusValues, setStatusValues] = useState([]);
  const [counts, setCounts] = useState(null);
  const [sheetError, setSheetError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = useRefreshAfterSave();
  const queryClient = useQueryClient();

  const restart = () => { setStep("upload"); setFile(null); setUpload(null); setStatusValues([]); setCounts(null); setNotice(""); };

  async function changeSheet(sheet) {
    setSheetError("");
    try {
      setUpload(await uploadImport(file, { sheet }));
    } catch (err) {
      setSheetError(err.message);
    }
  }

  async function afterChange() {
    await refresh();
    await queryClient.invalidateQueries({ queryKey: ["imports"] });
  }

  return (
    <>
      <PageHeader
        title="Import from a spreadsheet"
        subtitle="Bring in the buyers and document statuses you already track. You review everything before it is saved."
        actions={<Link to="/import/history" className="btn">Past imports</Link>}
      />
      <ImportStepper current={step} />
      {notice ? <p className="banner banner-ok" role="status">{notice}</p> : null}
      {sheetError ? <p className="form-error" role="alert">{sheetError}</p> : null}
      {step === "upload" ? (
        <UploadStep onUploaded={(result, chosen) => { setUpload(result); setFile(chosen); setStep("columns"); }} />
      ) : null}
      {step === "columns" && upload ? (
        <ColumnMappingStep
          key={upload.id}
          upload={upload}
          onChangeSheet={changeSheet}
          onBack={restart}
          onSaved={(response) => {
            setStatusValues(response.status_values);
            setStep(response.status_values.length ? "statuses" : "review");
          }}
        />
      ) : null}
      {step === "statuses" ? (
        <StatusMappingStep batchId={upload.id} values={statusValues} onBack={() => setStep("columns")} onSaved={() => setStep("review")} />
      ) : null}
      {step === "review" ? (
        <DryRunStep
          batchId={upload.id}
          filename={upload.filename}
          onBack={() => setStep(statusValues.length ? "statuses" : "columns")}
          onCommitted={async (result) => { setCounts(result.counts); setStep("done"); await afterChange(); }}
        />
      ) : null}
      {step === "done" ? (
        <DoneStep batchId={upload.id} filename={upload.filename} counts={counts} onAnother={restart} onUndone={async () => { await afterChange(); restart(); setNotice("The import was undone and everything it created has been removed."); }} />
      ) : null}
    </>
  );
}
