// Mirrors the backend `compare` tool result after normalize.wire.wire_response.
// Keep this close to the Python Pydantic models in normalize/query_models.py.
export type CitationConstituent = {
  source_url?: string;
  age_hours?: number;
  rate_unit?: string;
};

export type Citation = {
  age_hours?: number;
  source_url?: string;
  json_path?: string;
  fetched_at?: string;
  snapshot?: { provider: string; snapshot_iso: string; filename: string };
  synthesis?: { rule: string; formula: string };
  composite?: CitationConstituent[];
};

export type CompareRow = {
  provider: string;
  instance_type: string;
  region_native?: string;
  vcpu_actual?: number;
  ram_gb_actual?: number;
  hourly_usd?: number;
  monthly_usd?: number;
  synthesized?: boolean;
  citation?: Citation;
};

export type CompareResult = {
  request?: { vcpu?: number; ram_gb?: number; region?: string; family?: string };
  results?: CompareRow[];
  ranked_by?: string;
  unmet_requirements?: Array<{ provider: string; reason: string }>;
  data_quality?: { overall_status?: string };
};

export type CompareArgs = {
  vcpu?: number;
  ram_gb?: number;
  region?: string;
  family?: string;
  providers?: string[];
  expand?: string;
};
