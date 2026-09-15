/** Wire shapes of `/api/v1/analytics/*` (ADR-079), mirrored from the
 *  station's JSON so a server-side rename breaks the build rather than
 *  quietly blanking a chart -- the same rule `types.ts` states for the rest
 *  of the API. Every count here is of detections, never of animals. */

export type AnalyticsView = 'series' | 'hours' | 'taxa' | 'span'
export type SeriesGrain = 'day' | 'night' | 'week' | 'month'
export type TaxaGrain = 'day' | 'week' | 'month'
export type ColumnsGrain = 'day' | 'week'

export interface AnalyticsParams {
  range: string
  grain?: SeriesGrain | TaxaGrain
  columns?: ColumnsGrain
  group?: string | null
  label?: string | null
}

export interface DateRangeInfo {
  name: string
  first_date: string
  last_date: string
  label: string
  days: number
}

/** Fields every view carries: what was excluded, and how much of the range
 *  was actually built. `days_built` below `range.days` means holes. */
export interface Exclusions {
  excluded_synthetic_count: number
  excluded_withdrawn_count: number
  excluded_rejected_count: number
  days_built: number
}

export interface SeriesBucket {
  key: string
  start_date: string
  end_date: string
  detections: number
  groups: Record<string, number>
  seconds_from_microphone: number
  seconds_paused: number
  per_captured_hour: number | null
  days_built: number
  days_unbuilt: number
  partial: boolean
}

export interface SeriesPayload extends Exclusions {
  range: DateRangeInfo
  grain: SeriesGrain
  group: string | null
  label: string | null
  buckets: SeriesBucket[]
  note: string
}

export interface SolarHours {
  sunrise: number | null
  sunset: number | null
  civil_dawn: number | null
  civil_dusk: number | null
}

export interface HoursColumn {
  key: string
  start_date: string
  end_date: string
  hours: number[]
  captured: number[]
  days_built: number
  partial: boolean
  solar: SolarHours | null
}

export interface HoursPayload extends Exclusions {
  range: DateRangeInfo
  columns_grain: ColumnsGrain
  group: string | null
  label: string | null
  timezone: string
  columns: HoursColumn[]
  profile: number[]
  profile_captured: number[]
  note: string
}

export interface TaxaColumn {
  key: string
  start_date: string
  end_date: string
  days_built: number
  seconds_from_microphone: number
}

export interface TaxaRow {
  taxonomic_group: string
  label: string
  scientific_name: string | null
  detections: number
  best_score: number
  first_date: string
  last_date: string
  days_heard: number
  cells: number[]
}

export interface TaxaPayload extends Exclusions {
  range: DateRangeInfo
  grain: TaxaGrain
  group: string | null
  columns: TaxaColumn[]
  rows: TaxaRow[]
  total_rows: number
  note: string
}

export interface SpanDay {
  date: string
  built: boolean
  complete?: boolean
  detections?: number
  seconds_from_microphone?: number
  per_captured_hour?: number | null
  first?: number | null
  last?: number | null
  p05?: number | null
  p95?: number | null
  sunrise?: number | null
  sunset?: number | null
  civil_dawn?: number | null
  civil_dusk?: number | null
  daylight_hours?: number | null
  daylight_detections?: number
  daylight_per_captured_hour?: number | null
  night_hours?: number | null
  night_known?: boolean
  night_detections?: number | null
  night_per_captured_hour?: number | null
}

export interface SpanPayload extends Exclusions {
  range: DateRangeInfo
  group: string
  timezone: string
  coordinates_set: boolean
  days: SpanDay[]
  note: string
}

export interface AnalyticsStatus {
  timezone: string
  builder_version: string
  days_built: number
  first_date: string | null
  last_date: string | null
  first_detection_date: string | null
  days_missing: number
  last_built_at: string | null
  age_seconds: number | null
  detections_rolled_up: number
  today: string
  coordinates_set: boolean
}

export interface Question {
  id: string
  title: string
  question: string
  expect: string
  view: AnalyticsView
  params: AnalyticsParams
}

export interface SavedReport {
  id: string
  name: string
  question: string
  view: AnalyticsView
  params: AnalyticsParams
  created_at: string
  created_by: string
}
