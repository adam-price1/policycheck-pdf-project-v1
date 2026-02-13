/**
 * Crawl API functions.
 */
import client from './client';
import type { Crawl, CrawlConfig, CrawlStatusResponse } from '../types';

export const crawlApi = {
  startCrawl: async (config: CrawlConfig): Promise<Crawl> => {
    const response = await client.post<Crawl>('/api/crawl/start', config);
    return response.data;
  },

  getCrawlStatus: async (crawlId: number): Promise<CrawlStatusResponse> => {
    const response = await client.get<CrawlStatusResponse>(`/api/crawl/${crawlId}/status`);
    return response.data;
  },
};

export const startCrawl = crawlApi.startCrawl;
export const getCrawlStatus = crawlApi.getCrawlStatus;
