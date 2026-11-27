import type { APIRoute } from 'astro';
import { formatAdsTxtBody, getAdsConfig } from '../lib/ads';

export const GET: APIRoute = () => {
  const { adsenseClient } = getAdsConfig();
  return new Response(formatAdsTxtBody(adsenseClient), {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
};
