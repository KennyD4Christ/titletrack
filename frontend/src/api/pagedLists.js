import { api } from "./client.js";

const PAGE_SIZE = 200;   // the most the server allows per page (max_page_size in api/pagination.py)
const MAX_PAGES = 50;    // a safety stop (10,000 rows): if it is ever reached, `count` stays larger than the rows returned

/** Every row of a paginated list, following the pages until the server reports no next one.
 *  Returns {count, results}; a caller can tell the list is incomplete when count > results.length. */
async function fetchAllPages(path, params) {
  const results = [];
  let count = 0;
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const data = await api.get(path, { ...params, page, page_size: PAGE_SIZE });
    results.push(...data.results);
    count = data.count;
    if (!data.next) break;
  }
  return { count, results };
}

/** Every active catalogue entry. Everyone can read the catalogue (view_reference). */
export const listAllActiveDocumentTypes = () => fetchAllPages("/api/document-types/", { is_active: true });

/** Every catalogue entry, active or not, for the catalogue page. */
export const listAllDocumentTypes = () => fetchAllPages("/api/document-types/", {});

/** Every document of one sale. A sale id is required: the request helper drops empty query values, and without
 *  `sale` the server would answer with documents from every active sale in the company. */
export const listAllDocumentsOfSale = (saleId) => {
  if (saleId === undefined || saleId === null || saleId === "") {
    return Promise.reject(new Error("listAllDocumentsOfSale needs a sale id."));
  }
  return fetchAllPages("/api/sale-documents/", { sale: saleId });
};
