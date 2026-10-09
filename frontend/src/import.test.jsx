import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import upload from "./test/fixtures/import_upload.json";
import mapping from "./test/fixtures/import_mapping.json";
import dryrun from "./test/fixtures/import_dryrun.json";
import commit from "./test/fixtures/import_commit.json";
import history from "./test/fixtures/import_list.json";
import undoBlocked from "./test/fixtures/import_undo_blocked.json";
import issuesCsv from "./test/fixtures/import_issues.csv?raw";

const id = upload.id;
const body = (c) => JSON.parse(c.init.body);
const calls = (api, method, end) => api.calls.filter((c) => c.method === method && c.url.split("?")[0].endsWith(end));
const readBlob = (blob) => new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.readAsText(blob); });
const csv = (text) => new Response(text, { status: 200, headers: { "Content-Type": "text/csv" } });

const routes = (extra = {}, role = "manager") =>
  baseRoutes(role, {
    "POST /api/imports/": () => json(upload, 201),
    [`POST /api/imports/${id}/mapping/`]: mapping,
    [`POST /api/imports/${id}/status-mapping/`]: { status_mapping: {} },
    [`POST /api/imports/${id}/dry-run/`]: dryrun,
    [`GET /api/imports/${id}/errors.csv/`]: () => csv(issuesCsv),
    [`POST /api/imports/${id}/commit/`]: commit,
    [`POST /api/imports/${id}/undo/`]: { status: "undone" },
    "GET /api/imports/": history,
    ...extra,
  });

const chooseFile = async () => {
  await userEvent.upload(await screen.findByLabelText(/Spreadsheet/), new File(["x"], "sample_register.xlsx"));
  await userEvent.click(screen.getByRole("button", { name: "Upload and continue" }));
};
const toColumns = async (extra) => {
  const api = mockApi(routes(extra));
  renderApp("/import");
  await chooseFile();
  await screen.findByRole("heading", { name: "Match your columns" });
  return api;
};
const toStatuses = async (extra) => {
  const api = await toColumns(extra);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "What do your status words mean?" });
  return api;
};
const toReview = async (extra) => {
  const api = await toStatuses(extra);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Here is what will happen" });
  return api;
};

describe("step 1: upload", () => {
  it("needs a file, sends it, and moves on to matching columns", async () => {
    const api = mockApi(routes());
    renderApp("/import");
    const go = await screen.findByRole("button", { name: "Upload and continue" });
    expect(go).toBeDisabled();
    await chooseFile();
    expect(await screen.findByRole("heading", { name: "Match your columns" })).toBeInTheDocument();
    const form = calls(api, "POST", "/api/imports/")[0].init.body;
    expect(form.get("file").name).toBe("sample_register.xlsx");
    expect(form.get("sheet")).toBeNull();
    expect(screen.getByText(/We read 6 rows from/)).toBeInTheDocument();
    expect(screen.getByText(/column titles on row 3/)).toBeInTheDocument();
  });

  it("shows the server's reason when a file is refused", async () => {
    mockApi(routes({ "POST /api/imports/": () => json({ detail: "Only .xlsx and .csv files can be imported." }, 400) }));
    renderApp("/import");
    await chooseFile();
    expect(await screen.findByRole("alert")).toHaveTextContent("Only .xlsx and .csv files can be imported.");
  });

  it("offers a downloadable sample file", async () => {
    const blobs = [];
    URL.createObjectURL = (b) => { blobs.push(b); return "blob:sample"; };
    URL.revokeObjectURL = () => {};
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    mockApi(routes());
    renderApp("/import");
    await userEvent.click(await screen.findByRole("button", { name: "Download a sample file" }));
    expect(click).toHaveBeenCalled();
    expect(await readBlob(blobs[0])).toContain("Estate,Plot No,Buyer Name,Price,Amount Paid");
    click.mockRestore();
  });
});

describe("step 2: match columns", () => {
  it("pre-fills our guesses and shows examples from the file", async () => {
    await toColumns();
    expect(screen.getByLabelText(/^Estate/)).toHaveValue("Estate");
    expect(screen.getByLabelText(/^Price/)).toHaveValue("Price");
    expect(screen.getByLabelText("Deed of Assignment")).toHaveValue("Deed Status");
    expect(screen.getByLabelText("Allocation Letter")).toHaveValue("");            // not in this file
    expect(screen.getAllByText(/Examples: /).length).toBeGreaterThan(5);
    expect(screen.getByText(/Examples: .*Tunde Bakare/)).toBeInTheDocument();
  });

  it("will not continue without a required column, or with a column used twice", async () => {
    await toColumns();
    const next = screen.getByRole("button", { name: "Continue" });
    await userEvent.selectOptions(screen.getByLabelText(/^Price/), "Choose a column…");
    expect(next).toBeDisabled();
    expect(screen.getByText(/Still needed: Price/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/^Price/), "Amount Paid");
    expect(next).toBeDisabled();
    expect(screen.getByText(/Each column can be used once/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/^Price/), "Price");
    expect(next).toBeEnabled();
  });

  it("sends the mapping, leaving out what is not in the file", async () => {
    const api = await toColumns();
    await userEvent.selectOptions(screen.getByLabelText(/^Buyer phone/), "Not in my file");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "What do your status words mean?" });
    const sent = body(calls(api, "POST", "/mapping/")[0]).mapping;
    const expected = { ...upload.suggested_mapping };
    delete expected.phone;
    expect(sent).toEqual(expected);
  });

  it("lets the person pick another sheet, which reads the file again", async () => {
    const second = { ...upload, id: 99, sheet_name: "Phase 2" };
    const api = await toColumns({ "POST /api/imports/": (url, init) => json(init.body.get("sheet") ? second : { ...upload, sheets: ["Sales Register", "Phase 2"], sheet_name: "Sales Register" }, 201) });
    await userEvent.selectOptions(screen.getByLabelText("Sheet"), "Phase 2");
    await waitFor(() => expect(calls(api, "POST", "/api/imports/")).toHaveLength(2));
    expect(calls(api, "POST", "/api/imports/")[1].init.body.get("sheet")).toBe("Phase 2");
  });
});

describe("step 3: match statuses", () => {
  it("lists each word with how often it appears, pre-filled where we are sure", async () => {
    await toStatuses();
    for (const v of mapping.status_values) {
      const select = screen.getByLabelText(`Meaning of ${v.key === "(blank)" ? "blank" : v.key}`);
      expect(select).toHaveValue(v.suggested);
    }
    expect(screen.getByText("(left blank)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("will not guess a word it does not know", async () => {
    const unknown = { ...mapping, status_values: [...mapping.status_values, { key: "ready soon", example: "Ready soon", count: 2, suggested: null, chosen: null }] };
    const api = await toStatuses({ [`POST /api/imports/${id}/mapping/`]: unknown });
    const next = screen.getByRole("button", { name: "Continue" });
    expect(next).toBeDisabled();
    expect(screen.getByText(/We could not guess this one/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Meaning of ready soon"), "In progress");
    expect(next).toBeEnabled();
    await userEvent.click(next);
    await screen.findByRole("heading", { name: "Here is what will happen" });
    expect(body(calls(api, "POST", "/status-mapping/")[0]).status_mapping["ready soon"]).toBe("in_progress");
  });

  it("skips this step when no document columns were chosen", async () => {
    const api = mockApi(routes({ [`POST /api/imports/${id}/mapping/`]: { ...mapping, status_values: [] } }));
    renderApp("/import");
    await chooseFile();
    await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Here is what will happen" })).toBeInTheDocument();
    expect(calls(api, "POST", "/status-mapping/")).toHaveLength(0);
  });
});

describe("step 4: check, then import", () => {
  it("shows exactly what will happen before anything is saved", async () => {
    const api = await toReview();
    const s = dryrun.summary;
    const tile = (label) => screen.getByText(label, { selector: ".tile-label" }).closest(".tile");
    expect(tile("Will be imported")).toHaveTextContent(String(s.to_import));
    expect(tile("Errors (skipped)")).toHaveTextContent(String(s.errors));
    expect(tile("Warnings")).toHaveTextContent(String(s.warnings));
    expect(screen.getByText(/Nothing has been saved yet/)).toBeInTheDocument();
    expect(screen.getByText(/will be/, { selector: "p strong, p" })).toBeTruthy();
    expect(screen.getByText("Lekki Gardens", { exact: false })).toBeInTheDocument();
    expect(calls(api, "POST", "/commit/")).toHaveLength(0);
  });

  it("lists problems by spreadsheet row and can filter them", async () => {
    await toReview();
    const table = screen.getByRole("table", { name: "" , hidden: true });
    const rows = within(screen.getByRole("heading", { name: /What we found/ }).closest("section")).getAllByRole("row");
    expect(rows).toHaveLength(dryrun.issues.length + 1);
    expect(screen.getByText(/Invalid price/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Show"), "Errors");
    const filtered = within(screen.getByRole("heading", { name: /What we found/ }).closest("section")).getAllByRole("row");
    expect(filtered).toHaveLength(dryrun.issues.filter((i) => i.level === "error").length + 1);
    expect(table).toBeTruthy();
  });

  it("downloads the full issue list as a CSV", async () => {
    const blobs = [];
    URL.createObjectURL = (b) => { blobs.push(b); return "blob:issues"; };
    URL.revokeObjectURL = () => {};
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await toReview();
    await userEvent.click(screen.getByRole("button", { name: /Download all issues/ }));
    await waitFor(() => expect(blobs).toHaveLength(1));
    expect(await readBlob(blobs[0])).toBe(issuesCsv);
    click.mockRestore();
  });

  it("asks for confirmation, then commits once and shows the result", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = await toReview();
    await userEvent.click(screen.getByRole("button", { name: `Import ${dryrun.summary.to_import} rows` }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/You can undo the import afterwards/)).toBeInTheDocument();
    expect(calls(api, "POST", "/commit/")).toHaveLength(0);                       // still nothing saved
    await userEvent.click(within(dialog).getByRole("button", { name: "Yes, import" }));
    expect(await screen.findByRole("heading", { name: "Import complete" })).toBeInTheDocument();
    expect(body(calls(api, "POST", "/commit/")[0])).toEqual({ confirm: true });
    const c = commit.counts;
    expect(screen.getByText("Sales added").nextSibling).toHaveTextContent(String(c.sales_created));
    expect(screen.getByText("Rows skipped").nextSibling).toHaveTextContent(String(c.rows_skipped));
    expect(screen.getByRole("link", { name: "Assign owners" })).toHaveAttribute("href", "/obligations?incomplete=1");
  });

  it("will not allow an import when nothing can be imported or statuses are unmatched", async () => {
    const blocked = { ...dryrun, summary: { ...dryrun.summary, to_import: 0, can_commit: false },
      unmapped_statuses: [{ key: "weird", example: "Weird", count: 1 }] };
    await toReview({ [`POST /api/imports/${id}/dry-run/`]: blocked });
    expect(screen.getByRole("button", { name: "Nothing to import" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("“Weird”");
  });

  it("explains a refused commit and lets the person re-check", async () => {
    const api = await toReview({ [`POST /api/imports/${id}/commit/`]: () => json({ detail: "The data changed since the dry run. Run the dry run again." }, 409) });
    await userEvent.click(screen.getByRole("button", { name: /^Import \d+ rows$/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Yes, import" }));
    expect(await screen.findByText("The data changed since the dry run. Run the dry run again.")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Not yet" }));
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(calls(api, "POST", "/dry-run/")).toHaveLength(2));
  });
});

describe("undo", () => {
  const finish = async (extra) => {
    const api = await toReview(extra);
    await userEvent.click(screen.getByRole("button", { name: /^Import \d+ rows$/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Yes, import" }));
    await screen.findByRole("heading", { name: "Import complete" });
    return api;
  };

  it("removes the import after confirmation and returns to a fresh upload", async () => {
    const api = await finish();
    await userEvent.click(screen.getByRole("button", { name: "Undo this import" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Yes, undo it" }));
    expect(await screen.findByText(/The import was undone/)).toBeInTheDocument();
    expect(calls(api, "POST", "/undo/")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Upload your spreadsheet" })).toBeInTheDocument();
  });

  it("says why an undo is not possible", async () => {
    await finish({ [`POST /api/imports/${id}/undo/`]: () => json(undoBlocked, 409) });
    await userEvent.click(screen.getByRole("button", { name: "Undo this import" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Yes, undo it" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This import cannot be undone.");
    expect(alert).toHaveTextContent(undoBlocked.reasons[0]);
    expect(screen.getByRole("heading", { name: "Import complete" })).toBeInTheDocument();     // still imported
  });
});

describe("past imports and access", () => {
  it("lists past imports and offers Undo only for the one that is in place", async () => {
    mockApi(routes());
    renderApp("/import/history");
    expect(await screen.findByRole("heading", { name: "Past imports" })).toBeInTheDocument();
    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(history.results.length + 1);
    expect(screen.getAllByRole("button", { name: "Undo this import" })).toHaveLength(history.results.filter((r) => r.status === "committed").length);
    expect(screen.getByText("Not finished (nothing was saved)")).toBeInTheDocument();
    expect(screen.getByText("Removed")).toBeInTheDocument();
  });

  it("is for MD and Manager only", async () => {
    for (const role of ["staff", "sales", "viewer"]) {
      mockApi(routes({}, role));
      const { unmount } = renderApp("/import");
      expect(await screen.findByRole("heading", { name: "You do not have access to this page" })).toBeInTheDocument();
      unmount();
    }
    mockApi(routes({}, "md"));
    renderApp("/import");
    expect(await screen.findByRole("heading", { name: "Upload your spreadsheet" })).toBeInTheDocument();
  });
});
