/**
 * Documents API functions.
 */
import client from './client';
import type { Document } from '../types';

export interface DocumentFilters {
  status?: string;
  classification?: string;
  country?: string;
  min_confidence?: number;
  skip?: number;
  limit?: number;
  page?: number;
  search?: string;
  crawl_session_id?: number;
}

export interface DocumentListResponse {
  documents: Document[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  /* computed helpers for the UI */
  items: Document[];
  pages: number;
  page: number;
  page_size: number;
}

const EMPTY_RESPONSE: DocumentListResponse = {
  documents: [],
  total: 0,
  limit: 20,
  offset: 0,
  has_more: false,
  items: [],
  pages: 1,
  page: 1,
  page_size: 20,
};

export const documentsApi = {
  getDocuments: async (filters: DocumentFilters = {}): Promise<DocumentListResponse> => {
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          params.append(key, value.toString());
        }
      });
      const response = await client.get('/api/documents', { params });
      const data = response.data || {};
      const docs = data.documents || [];
      const total = data.total || 0;
      const limit = data.limit || filters.limit || 20;
      return {
        ...EMPTY_RESPONSE,
        ...data,
        documents: docs,
        items: docs,
        total,
        pages: Math.ceil(total / limit) || 1,
        page: filters.page || 1,
        page_size: limit,
      };
    } catch {
      return EMPTY_RESPONSE;
    }
  },

  getDocument: async (documentId: number): Promise<Document> => {
    const response = await client.get<Document>(`/api/documents/${documentId}`);
    return response.data;
  },

  approveDocument: async (documentId: number): Promise<Document> => {
    const response = await client.put<Document>(`/api/documents/${documentId}/approve`);
    return response.data;
  },

  reclassifyDocument: async (documentId: number, classification: string): Promise<Document> => {
    const response = await client.put<Document>(
      `/api/documents/${documentId}/reclassify`,
      { classification }
    );
    return response.data;
  },

  deleteDocument: async (documentId: number): Promise<void> => {
    await client.delete(`/api/documents/${documentId}`);
  },

  archiveDocument: async (documentId: number): Promise<Document> => {
    const response = await client.put<Document>(`/api/documents/${documentId}/archive`);
    return response.data;
  },
};

export const listDocuments = documentsApi.getDocuments;
export const searchLibrary = documentsApi.getDocuments;
export const approveDocument = documentsApi.approveDocument;
export const archiveDocument = documentsApi.archiveDocument;
export const deleteDocument = documentsApi.deleteDocument;
