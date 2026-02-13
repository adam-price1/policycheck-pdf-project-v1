import { useState, useEffect, useRef } from "react";
import { startCrawl, getCrawlStatus } from "../api/crawl";
import StatusBadge from "../components/StatusBadge";
import type { CrawlStatusResponse } from "../types";

const DEFAULT_CONFIG = {
  country: "NZ",
  seed_urls: [
    "https://www.aainsurance.co.nz/products",
    "https://www.ami.co.nz/insurance",
    "https://www.tower.co.nz/products",
  ],
  policy_types: ["Life", "Home", "Motor"],
  keywords: ["PDS", "Policy Wording", "Fact Sheet", "TMD"],
  max_pages: 1000,
  max_time: 60,
};

export default function CrawlPage() {
  const [country, setCountry] = useState(DEFAULT_CONFIG.country);
  const [seedUrls, setSeedUrls] = useState(DEFAULT_CONFIG.seed_urls.join("\n"));
  const [maxPages, setMaxPages] = useState(DEFAULT_CONFIG.max_pages);
  const [maxTime, setMaxTime] = useState(DEFAULT_CONFIG.max_time);
  const [starting, setStarting] = useState(false);
  const [crawlStatus, setCrawlStatus] = useState<CrawlStatusResponse | null>(null);
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  async function handleStart() {
    setError("");
    setStarting(true);
    try {
      const config = {
        country,
        seed_urls: seedUrls.split("\n").map((u) => u.trim()).filter(Boolean),
        policy_types: DEFAULT_CONFIG.policy_types,
        keywords: DEFAULT_CONFIG.keywords,
        max_pages: maxPages,
        max_time: maxTime,
      };
      const result = await startCrawl(config);
      pollStatus(result.crawl_id);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || "Failed to start crawl.";
      setError(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    setStarting(false);
  }

  function pollStatus(crawlId: number) {
    getCrawlStatus(crawlId).then(setCrawlStatus).catch(() => {});
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const status = await getCrawlStatus(crawlId);
        setCrawlStatus(status);
        if (status.status === "completed" || status.status === "failed" || status.status === "stopped") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, 2000);
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">Crawl Manager</h1>
        <p className="mt-1 text-sm text-gray-500">Configure and trigger web crawls to discover insurance policy documents</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Config Panel */}
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">Crawl Configuration</h2>

          {error && (
            <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
          )}

          <label className="mb-1 block text-sm font-medium text-gray-700">Country</label>
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="mb-4 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm"
          >
            <option value="NZ">New Zealand</option>
            <option value="AU">Australia</option>
            <option value="UK">United Kingdom</option>
          </select>

          <label className="mb-1 block text-sm font-medium text-gray-700">Seed URLs (one per line)</label>
          <textarea
            value={seedUrls}
            onChange={(e) => setSeedUrls(e.target.value)}
            rows={4}
            className="mb-4 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs"
          />

          <div className="mb-4 grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Max Pages</label>
              <input type="number" value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} min={1} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Timeout (min)</label>
              <input type="number" value={maxTime} onChange={(e) => setMaxTime(Number(e.target.value))} min={1} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" />
            </div>
          </div>

          <div className="mb-4 rounded-lg bg-gray-50 p-3">
            <p className="mb-1 text-xs font-medium text-gray-600">Policy Types</p>
            <div className="flex flex-wrap gap-1.5">
              {DEFAULT_CONFIG.policy_types.map((t) => (
                <span key={t} className="rounded-full bg-primary-100 px-2.5 py-0.5 text-xs font-medium text-primary-700">{t}</span>
              ))}
            </div>
            <p className="mb-1 mt-3 text-xs font-medium text-gray-600">Keywords</p>
            <div className="flex flex-wrap gap-1.5">
              {DEFAULT_CONFIG.keywords.map((k) => (
                <span key={k} className="rounded-full bg-gray-200 px-2.5 py-0.5 text-xs font-medium text-gray-700">{k}</span>
              ))}
            </div>
          </div>

          <button
            onClick={handleStart}
            disabled={starting || crawlStatus?.status === "running"}
            className="w-full rounded-lg bg-primary-600 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary-700 disabled:opacity-50"
          >
            {starting ? "Starting..." : crawlStatus?.status === "running" ? "Crawl in progress..." : "Start Crawl"}
          </button>
        </div>

        {/* Status Panel */}
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">Crawl Status</h2>

          {!crawlStatus ? (
            <div className="py-12 text-center text-sm text-gray-400">
              No active crawl. Configure and start one to see progress.
            </div>
          ) : (
            <div>
              <div className="mb-4 flex items-center justify-between">
                <span className="text-sm text-gray-500">Crawl #{crawlStatus.id}</span>
                <StatusBadge status={crawlStatus.status} />
              </div>

              {/* Progress bar */}
              <div className="mb-4">
                <div className="mb-1 flex justify-between text-xs text-gray-500">
                  <span>Progress</span>
                  <span>{crawlStatus.progress_pct ?? 0}%</span>
                </div>
                <div className="h-3 overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full bg-primary-500 transition-all duration-500"
                    style={{ width: `${crawlStatus.progress_pct ?? 0}%` }}
                  />
                </div>
              </div>

              {/* Stats grid */}
              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: "Pages Scanned", value: crawlStatus.pages_scanned ?? 0, color: "text-blue-700" },
                  { label: "PDFs Found", value: crawlStatus.pdfs_found ?? 0, color: "text-emerald-700" },
                  { label: "PDFs Downloaded", value: crawlStatus.pdfs_downloaded ?? 0, color: "text-green-700" },
                  { label: "Errors", value: crawlStatus.errors_count ?? 0, color: "text-red-600" },
                ].map((stat) => (
                  <div key={stat.label} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
                    <p className="text-xs text-gray-500">{stat.label}</p>
                    <p className={`text-xl font-bold ${stat.color}`}>{stat.value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
