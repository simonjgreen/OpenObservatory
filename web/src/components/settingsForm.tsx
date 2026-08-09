/** The pieces the settings page and the guided first-run flow share.
 *
 *  Both surfaces edit the same fields through the same endpoint, so they use
 *  one field renderer and one diffing rule. Two renderers would eventually
 *  disagree about what "changed" means, and the disagreement would show up as
 *  a setting that looks saved and is not — the exact dishonesty ADR-048 sets
 *  out to prevent.
 *
 *  Nothing here validates. The API is the only validator (Pydantic v2, plus
 *  the cross-field rules in `site_settings.validate_merged`), because a form
 *  that guesses the rules will eventually guess a different set than the
 *  station enforces. `min`/`max`/`step` attributes are present as *hints* for
 *  the browser's own affordances — the server still checks them, and its
 *  message is what the operator is shown when they disagree.
 */

import type { ReactNode } from 'react'

export type Tier = 'live' | 'restart'
export type FieldKind = 'bool' | 'int' | 'float' | 'enum' | 'csv' | 'text'

export interface SettingsField {
  name: string
  category: string
  tier?: Tier
  kind?: FieldKind
  label?: string
  help?: string | null
  unit?: string | null
  minimum?: number | null
  maximum?: number | null
  choices?: string[]
  danger?: string | null
  secret: boolean
  restart_required: boolean
  note: string | null
  default?: string | number | boolean | null
  value?: string | number | boolean | null
  is_set?: boolean
  pending_restart?: boolean
}

export interface SettingsCategory {
  id: string
  title: string
  description: string
  hidden?: boolean
}

export interface SettingsPayload {
  fields: SettingsField[]
  categories?: SettingsCategory[]
  non_editable?: Array<{ name: string; reason: string }>
  pending_restart: string[]
  location_configured: boolean
  saved?: string[]
}

export type Draft = Record<string, string | boolean>

/** The canonical string form of a field's current value.
 *
 *  One function, used both to seed the draft and to decide whether a field
 *  changed, so "unchanged" can never mean two different things.
 */
export function currentValue(field: SettingsField): string | boolean {
  if (field.secret) return '' // write-only; empty means "unchanged"
  if (typeof field.value === 'boolean') return field.value
  return field.value == null ? '' : String(field.value)
}

export function draftFrom(payload: SettingsPayload): Draft {
  const draft: Draft = {}
  for (const field of payload.fields) draft[field.name] = currentValue(field)
  return draft
}

/** The value a "reset" control writes into the draft.
 *
 *  An explicit default, not a blank: the server treats blank as "restore the
 *  default" too, but showing the operator the number they are about to save is
 *  the difference between a reference point and a leap of faith.
 */
export function defaultDraftValue(field: SettingsField): string | boolean {
  if (typeof field.default === 'boolean') return field.default
  return field.default == null ? '' : String(field.default)
}

export function isAtDefault(field: SettingsField, value: string | boolean): boolean {
  return value === defaultDraftValue(field)
}

/** The PUT body: only what differs, and secrets only when actually typed. */
export function changedBody(payload: SettingsPayload, draft: Draft, only?: string[]): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  for (const field of payload.fields) {
    if (only && !only.includes(field.name)) continue
    const value = draft[field.name]
    if (field.secret) {
      if (typeof value === 'string' && value !== '') body[field.name] = value
      continue
    }
    if (value !== currentValue(field)) body[field.name] = value
  }
  return body
}

/** Dangerous fields the operator has changed but not acknowledged. */
export function unacknowledgedDangers(
  payload: SettingsPayload,
  body: Record<string, unknown>,
  acknowledged: Set<string>,
): SettingsField[] {
  return payload.fields.filter(
    (field) => field.danger && field.name in body && !acknowledged.has(field.name),
  )
}

export function fieldLabel(field: SettingsField): string {
  return field.label ?? field.name.replace(/_/g, ' ')
}

interface FieldProps {
  field: SettingsField
  value: string | boolean
  error?: string
  acknowledged: boolean
  onChange: (value: string | boolean) => void
  onAcknowledge: (value: boolean) => void
}

function tierTag(field: SettingsField): ReactNode {
  if (field.pending_restart) {
    return <em className="restart-tag pending"> saved, not yet in force</em>
  }
  if (field.restart_required) return <em className="restart-tag"> restart to apply</em>
  return null
}

/** One field, with everything an operator needs to change it safely: what it
 *  is, what it is now, what it shipped as, whether it is in force, and — for
 *  the ones that can cost recordings — what it will do. */
export function SettingField({
  field,
  value,
  error,
  acknowledged,
  onChange,
  onAcknowledge,
}: FieldProps) {
  const label = fieldLabel(field)
  const changed = !field.secret && value !== currentValue(field)
  const showDanger = Boolean(field.danger) && changed

  const control = () => {
    if (typeof value === 'boolean') {
      return (
        <input
          id={`set-${field.name}`}
          type="checkbox"
          checked={value}
          onChange={(event) => onChange(event.target.checked)}
        />
      )
    }
    if (field.kind === 'enum' && (field.choices?.length ?? 0) > 0) {
      return (
        <select
          id={`set-${field.name}`}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {field.choices!.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      )
    }
    const numeric = field.kind === 'int' || field.kind === 'float'
    return (
      <input
        id={`set-${field.name}`}
        type={field.secret ? 'password' : numeric ? 'number' : 'text'}
        inputMode={numeric ? 'decimal' : undefined}
        step={field.kind === 'int' ? 1 : 'any'}
        min={field.minimum ?? undefined}
        max={field.maximum ?? undefined}
        value={value}
        placeholder={
          field.secret ? (field.is_set ? '(set — leave blank to keep)' : '(not set)') : ''
        }
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }

  return (
    <div className={`settings-field ${typeof value === 'boolean' ? 'settings-field-bool' : ''}`}>
      <label htmlFor={`set-${field.name}`}>
        <span className="settings-field-name">
          {label}
          {field.unit && <span className="settings-unit"> ({field.unit})</span>}
          {tierTag(field)}
        </span>
      </label>
      <div className="settings-control">
        {control()}
        {!field.secret && field.default != null && field.default !== '' && (
          <button
            type="button"
            className="linklike settings-reset"
            title={`Shipped default: ${String(field.default)}`}
            disabled={isAtDefault(field, value)}
            onClick={() => onChange(defaultDraftValue(field))}
          >
            default: {String(field.default)}
          </button>
        )}
      </div>
      {field.help && <p className="settings-help">{field.help}</p>}
      {field.note && <p className="settings-help settings-note-field">{field.note}</p>}
      {showDanger && (
        <label className="checkbox settings-danger">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => onAcknowledge(event.target.checked)}
          />
          <span>{field.danger} — I understand.</span>
        </label>
      )}
      {error && <em className="field-error">{error}</em>}
    </div>
  )
}
