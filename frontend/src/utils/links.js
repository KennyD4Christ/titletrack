import { toQuery } from "../api/client.js";

/** Every dashboard number links to the obligations list with the matching filter. */
export const obligationsLink = (params = {}) => `/obligations${toQuery(params)}`;
