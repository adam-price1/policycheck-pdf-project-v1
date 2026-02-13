import { useEffect, useState, useCallback } from "react";
import { listDocuments } from "../api/documents";
import { useAuth } from "../context/AuthContext";
import LoadingSpinner from "../components/LoadingSpinner";
import type { Document } from "../types";

export default function Review() {
  const { user } = useAuth();
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listDocuments({ limit: 100 });
      setDocs(resp?.items ?? resp?.documents ?? []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingSpinner />;

  const pending = docs.filter((d) => d.status === "pending");
  const validated = docs.filter((d) => d.status === "validated");

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">Review Queue</h1>
        <p className="mt-1 text-sm text-gray-500">
          {pending.length} pending review · {validated.length} validated
        </p>
      </div>

      {pending.length === 0 && validated.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white py-16 text-center">
          <p className="text-gray-500">No documents to review. Start a crawl first.</p>
        </div>
      ) : pending.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white py-16 text-center">
          <p className="text-gray-500">All documents have been reviewed!</p>
        </div>
      ) : (
        <div className="space-y-4">
          {pending.map((doc) => (
            <div key={doc.id} className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-all">
              <div className="mb-4 flex items-start justify-between">
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">{doc.insurer}</h3>
                  <p className="mt-1 text-sm text-gray-500">
                    {doc.classification} · {doc.policy_type} · {doc.country}
                  </p>
                </div>
                <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-700">
                  Pending
                </span>
              </div>

              <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <div>
                  <p className="text-xs text-gray-500">Confidence</p>
                  <p className="mt-0.5 text-sm font-semibold text-gray-900">{((doc.confidence ?? 0) * 100).toFixed(0)}%</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Source</p>
                  <p className="mt-0.5 text-sm font-medium text-gray-900 truncate">
                    {(() => { try { return new URL(doc.source_url).hostname; } catch { return doc.source_url; } })()}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">File Size</p>
                  <p className="mt-0.5 text-sm font-medium text-gray-900">
                    {doc.file_size ? `${(doc.file_size / 1024).toFixed(0)} KB` : "—"}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Type</p>
                  <p className="mt-0.5 text-sm font-medium text-gray-900">{doc.document_type}</p>
                </div>
              </div>

              {doc.warnings && doc.warnings.length > 0 && (
                <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
                  <p className="mb-2 text-xs font-semibold uppercase text-amber-800">
                    Warnings ({doc.warnings.length})
                  </p>
                  <ul className="space-y-1">
                    {doc.warnings.map((w, i) => (
                      <li key={i} className="text-sm text-amber-700">• {w}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
