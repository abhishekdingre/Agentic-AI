import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const API_BASE = 'https://api.mfapi.in/mf';

const server = new McpServer({
  name: 'sip-mutual-fund-data',
  version: '1.0.0',
});

server.tool(
  'search_mutual_fund',
  'Search Indian mutual fund schemes by name (via mfapi.in) to find a scheme code for get_fund_returns.',
  { query: z.string().min(2).describe('Fund name or partial name, e.g. "HDFC Flexi Cap"') },
  async ({ query }) => {
    const response = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}`);
    if (!response.ok) {
      return { content: [{ type: 'text', text: `Search failed: ${response.status} ${response.statusText}` }], isError: true };
    }
    const results = await response.json();
    return { content: [{ type: 'text', text: JSON.stringify(results.slice(0, 20), null, 2) }] };
  },
);

function parseMfapiDate(dateStr) {
  const [day, month, year] = dateStr.split('-').map(Number);
  return new Date(year, month - 1, day);
}

server.tool(
  'get_fund_returns',
  'Fetch NAV history for an Indian mutual fund scheme (via mfapi.in) and compute its annualized (CAGR) return, for use as a realistic expected-return input to a SIP calculation.',
  { schemeCode: z.union([z.string(), z.number()]).describe('mfapi.in scheme code, from search_mutual_fund') },
  async ({ schemeCode }) => {
    const response = await fetch(`${API_BASE}/${schemeCode}`);
    if (!response.ok) {
      return { content: [{ type: 'text', text: `Lookup failed: ${response.status} ${response.statusText}` }], isError: true };
    }
    const payload = await response.json();
    const navData = payload.data;
    if (!Array.isArray(navData) || navData.length < 2) {
      return { content: [{ type: 'text', text: 'Not enough NAV history to compute a return.' }], isError: true };
    }

    const latest = navData[0];
    const oldest = navData[navData.length - 1];
    const latestNav = parseFloat(latest.nav);
    const oldestNav = parseFloat(oldest.nav);
    const years = (parseMfapiDate(latest.date) - parseMfapiDate(oldest.date)) / (1000 * 60 * 60 * 24 * 365.25);
    const annualizedReturnPercent = years > 0 ? (Math.pow(latestNav / oldestNav, 1 / years) - 1) * 100 : null;

    const result = {
      schemeName: payload.meta?.scheme_name ?? null,
      schemeCode: payload.meta?.scheme_code ?? schemeCode,
      latestNav,
      latestDate: latest.date,
      oldestNav,
      oldestDate: oldest.date,
      yearsOfHistory: Math.round(years * 100) / 100,
      annualizedReturnPercent: annualizedReturnPercent !== null ? Math.round(annualizedReturnPercent * 100) / 100 : null,
    };

    return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
