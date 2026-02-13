import { useEffect, useState, useCallback } from "react";
import { searchLibrary } from "../api/documents";
import LoadingSpinner from "../components/LoadingSpinner";
import StatusBadge from "../components/StatusBadge";
import type { Document } from "../types";

export default function Library() {
  const [docs, setDocs] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [search, setSearch] = useState("");
  const [countryFilter, setCountryFilter] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await searchLibrary({
        search: search || undefined,
        country: countryFilter || undefined,
        page,
        limit: 20,
      });
      setDocs(resp?.items ?? resp?.documents ?? []);
      setTotal(resp?.total ?? 0);
      setPages(resp?.pages ?? 1);
    } catch { /* ignore */ }
    setLoading(false);
  }, [search, countryFilter, page]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">Policy Library</h1>
        <p className="mt-1 text-sm text-gray-500">{total} documents</p>
      </div>

      <div className="mb-5 flex flex-wrap gap-3">
        <input
          type="text"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          placeholder="Search library..."
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
        <select
          value={countryFilter}
          onChange={(e) => { setCountryFilter(e.target.value); setPage(1); }}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">All Countries</option>
          <option value="NZ">New Zealand</option>
          <option value="AU">Australia</option>
          <option value="UK">United Kingdom</option>
        </select>
      </div>

      {loading ? (
        <LoadingSpinner />
      ) : docs.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white py-16 text-center">
          <p className="text-gray-500">No documents found.</p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {docs.map((doc) => (
            <div key={doc.id} className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm hover:shadow-md transition-all">
              <div className="mb-3 flex items-start justify-between">
                <StatusBadge status={doc.status} />
                <span className="text-xs text-gray-400">{doc.country}</span>
              </div>
              <h3 className="mb-1 text-sm font-semibold text-gray-900 line-clamp-2">{doc.insurer}</h3>
              <p className="mb-3 text-xs text-gray-500">{doc.classification} · {doc.policy_type}</p>
              <div className="text-xs text-gray-400">
                Confidence: {((doc.confidence ?? 0) * 100).toFixed(0)}%
              </div>
            </div>
          ))}
        </div>
      )}

      {pages > 1 && (
        <div className="mt-6 flex items-center justify-between">
          <span className="text-sm text-gray-500">Page {page} of {pages}</span>
          <div className="flex gap-2">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="rounded-md border border-gray-300 px-3 py-1 text-sm disabled:opacity-40">Previous</button>
            <button onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page === pages} className="rounded-md border border-gray-300 px-3 py-1 text-sm disabled:opacity-40">Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
