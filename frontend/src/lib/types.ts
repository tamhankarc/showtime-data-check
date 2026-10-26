export type Role = "admin" | "viewer";
export interface Session {
  role: Role;
  username: string;
}
export interface Country {
  code: string;
  name: string;
  timezone?: string;
  enabled?: boolean;
}
export interface Connection {
  id: number;
  country_code: string;
  provider_code: string;
  provider_country_code?: string;
  timezone?: string;
  enabled: boolean;
  last_success_at?: string;
  last_status?: string;
  movie_count?: number;
  catalog_window_days?: number | null;
}
export interface ProviderMovie {
  id: number;
  provider_code: string;
  external_id: string;
  url_key?: string | null;
  name: string;
}
export interface ProviderMovieRecord extends ProviderMovie {
  country_code: string;
  original_name?: string;
  group_id?: number;
  group_name?: string;
  active: boolean;
  release_date?: string;
}
export interface Movie {
  id: number;
  name: string;
  country_code: string;
  provider_ids: ProviderMovie[];
  active: boolean;
}
export interface Bootstrap {
  countries: Country[];
  connections: Connection[];
  role: Role;
}
export interface Outcome {
  provider_code: string;
  country_code?: string;
  connection_id?: number;
  status: string;
  message?: string;
  fetched_at?: string;
  expires_at?: string;
}
export interface Job {
  id: string;
  status: string;
  progress?: number;
  message?: string;
  provider_outcomes?: Outcome[];
}
export interface Showtime {
  id: string;
  movie_name: string;
  theater_id: number | string;
  theater_name: string;
  chain?: string;
  address?: string;
  city?: string;
  country_code: string;
  date: string;
  time: string;
  published_format?: string;
  raw_format: string;
  format_mapped?: boolean;
  vip?: boolean;
  audio_language?: string;
  subtitle_language?: string;
  providers: string[];
  links: { name: string; url: string; kind: string }[];
  has_usable_link: boolean;
  imax: boolean;
}
export interface TheaterResult {
  id: string;
  movie_name: string;
  theater_id: number | string;
  theater_name: string;
  chain?: string;
  address?: string;
  city?: string;
  country_code: string;
  showtimes: number;
  first?: string;
  last?: string;
  formats: string[];
  providers: string[];
}
export interface CheckResult {
  status: string;
  rows: (Showtime | TheaterResult)[];
  total: number;
  page: number;
  page_size: number;
  metrics: {
    showtimes: number;
    theaters: number;
    no_usable_link: number;
    imax: number;
  };
  filters: { chains: string[]; formats: string[]; languages: string[] };
  provider_outcomes: Outcome[];
  expires_at?: string;
  selection?: {
    country_code: string;
    movie_ids: number[];
    provider_codes: string[];
  };
}
export type RecordData = Record<string, unknown>;
export interface ListPage<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
