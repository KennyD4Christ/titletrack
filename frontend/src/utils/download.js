/** Save text as a file in the browser (used for the issue list and the sample file). */
export function downloadText(filename, text, mime = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export const SAMPLE_CSV = [
  "Estate,Plot No,Buyer Name,Price,Amount Paid,Sale Date,Date Fully Paid,Phone,Deed Status,Survey Status",
  "Green Valley Estate,A-01,Ada Obi,25000000,25000000,03/02/2026,10/02/2026,08031112222,Done,Pending",
  "Green Valley Estate,A-02,Bayo Lawal,18000000,9000000,01/03/2026,,08051112222,Pending,",
].join("\n") + "\n";
