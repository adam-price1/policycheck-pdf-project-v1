import { useState, useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getCrawlStatus } from "../api/crawl";
import type { CrawlStatusResponse } from "../types";

export default function Progress() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const crawlIdParam = searchParams.get("crawl_id");
  const [crawl, setCrawl] = useState<CrawlStatusResponse | null>(null);
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!crawlIdParam) return;
    const id = Number(crawlIdParam);

    getCrawlStatus(id).then(setCrawl).catch(() => setError("Could not load crawl status"));

    pollRef.current = setInterval(async () => {
      try {
        const status = await getCrawlStatus(id);
        setCrawl(status);
        if (status.status === "completed" || status.status === "failed" || status.status === "stopped") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, 1500);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [crawlIdParam]);

  if (!crawlIdParam) {
    return (
      <div className="text-center py-20">
        <p className="text-gray-500 mb-4">No crawl in progress.</p>
        <button
          onClick={() => navigate("/crawl")}
          className="px-6 py-3 bg-primary-600 text-white rounded-lg font-semibold hover:bg-primary-700"
        >
          Go to Crawl Manager
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6 flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 mb-1">Crawl in Progress</h1>
          <p className="text-gray-600">
            {crawl?.status === "completed"
              ? "Crawl completed"
              : crawl?.status === "failed"
                ? "Crawl failed"
                : "Scanning websites..."}
          </p>
        </div>
        {(crawl?.status === "completed" || crawl?.status === "failed") && (
          <button
            onClick={() => navigate("/review")}
            className="px-6 py-3 bg-primary-600 text-white rounded-lg font-semibold hover:bg-primary-700"
          >
            View Results
          </button>
        )}
      </div>

      {error && (
        <div className="mb-6 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      <div className="bg-white rounded-xl shadow p-8 mb-6">
        <div className="mb-6">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm font-medium text-gray-700">Overall Progress</span>
            <span className="text-sm font-semibold text-primary-600">{crawl?.progress_pct ?? 0}%</span>
          </div>
          <div className="w-full h-3 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-primary-500 to-primary-600 transition-all duration-500"
              style={{ width: `${crawl?.progress_pct ?? 0}%` }}
            />
          </div>
        </div>

        <div className="grid grid-cols-4 gap-6">
          <div className="bg-gradient-to-br from-blue-50 to-blue-100 rounded-xl p-6">
            <div className="text-3xl font-bold text-blue-600">{crawl?.pages_scanned ?? 0}</div>
            <div className="text-sm font-medium text-blue-900 mt-1">Pages Scanned</div>
          </div>
          <div className="bg-gradient-to-br from-purple-50 to-purple-100 rounded-xl p-6">
            <div className="text-3xl font-bold text-purple-600">{crawl?.pdfs_found ?? 0}</div>
            <div className="text-sm font-medium text-purple-900 mt-1">PDFs Discovered</div>
          </div>
          <div className="bg-gradient-to-br from-green-50 to-green-100 rounded-xl p-6">
            <div className="text-3xl font-bold text-green-600">{crawl?.pdfs_downloaded ?? 0}</div>
            <div className="text-sm font-medium text-green-900 mt-1">PDFs Downloaded</div>
          </div>
          <div className="bg-gradient-to-br from-red-50 to-red-100 rounded-xl p-6">
            <div className="text-3xl font-bold text-red-600">{crawl?.errors_count ?? 0}</div>
            <div className="text-sm font-medium text-red-900 mt-1">Errors</div>
          </div>
        </div>
      </div>
    </div>
  );
}
