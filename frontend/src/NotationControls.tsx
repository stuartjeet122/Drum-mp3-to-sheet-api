import { useEffect, useState } from 'react';
import { LoaderCircle, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { defaultNotationOptions, updateNotation, type Job, type NotationOptions, type NotationSection } from './api';

export default function NotationControls({ job, onJobChange }: {
  job: Job;
  onJobChange: (job: Job) => void;
}) {
  const [options, setOptions] = useState<NotationOptions>(() => ({
    ...defaultNotationOptions,
    ...(job.notation_options || {}),
    sections: job.notation_options?.sections?.map(section => ({ ...section })) || [],
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const measureCount = Math.max(1, job.total_measures || 1);

  useEffect(() => {
    setOptions({
      ...defaultNotationOptions,
      ...(job.notation_options || {}),
      sections: job.notation_options?.sections?.map(section => ({ ...section })) || [],
    });
  }, [job.job_id]);

  function updateSection(index: number, patch: Partial<NotationSection>) {
    setOptions(current => ({
      ...current,
      sections: current.sections.map((section, row) => row === index ? { ...section, ...patch } : section),
    }));
  }

  async function save() {
    setSaving(true);
    setError('');
    try {
      const next = await updateNotation(job.job_id, options);
      onJobChange(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Notation could not be regenerated. Try again.');
    } finally {
      setSaving(false);
    }
  }

  return <section className="notation-controls" aria-labelledby="notation-options-title">
    <div className="notation-controls-heading">
      <div><span className="section-tag">ENGRAVING</span><h3 id="notation-options-title">Notation &amp; drum layout</h3></div>
      <span>EDITABLE SCORE</span>
    </div>
    <div className="notation-options-grid">
      <label><input type="checkbox" checked={options.show_dynamics} onChange={event => setOptions(current => ({ ...current, show_dynamics: event.target.checked }))} />Dynamics</label>
      <label><input type="checkbox" checked={options.show_accents} onChange={event => setOptions(current => ({ ...current, show_accents: event.target.checked }))} />Accent marks</label>
      <label><input type="checkbox" checked={options.show_sticking} onChange={event => setOptions(current => ({ ...current, show_sticking: event.target.checked }))} />R/L sticking</label>
      <label className="notation-field">Accent from velocity<input type="number" min="1" max="127" value={options.accent_threshold} onChange={event => setOptions(current => ({ ...current, accent_threshold: Math.max(1, Math.min(127, Number(event.target.value) || 1)) }))} /></label>
      <label className="notation-field">Measures per system<select value={options.measures_per_system} onChange={event => setOptions(current => ({ ...current, measures_per_system: Number(event.target.value) }))}>{[2, 3, 4, 5, 6, 8].map(value => <option key={value} value={value}>{value} measures</option>)}</select></label>
    </div>
    <div className="notation-sections-heading"><div><strong>Sections &amp; repeats</strong><span>Label a range of bars and optionally add repeat signs.</span></div><button className="secondary-button" onClick={() => setOptions(current => ({ ...current, sections: [...current.sections, { start_measure: 1, end_measure: Math.min(4, measureCount), label: 'Section', repeat: false }] }))} disabled={saving}><Plus size={14} />Add section</button></div>
    {options.sections.length > 0 && <div className="notation-section-list">{options.sections.map((section, index) => <div className="notation-section-row" key={index}>
      <label>From<input type="number" min="1" max={measureCount} value={section.start_measure} onChange={event => {
        const start = Math.max(1, Math.min(measureCount, Number(event.target.value) || 1));
        updateSection(index, { start_measure: start, end_measure: Math.max(start, section.end_measure) });
      }} /></label>
      <label>To<input type="number" min={section.start_measure} max={measureCount} value={section.end_measure} onChange={event => updateSection(index, { end_measure: Math.max(section.start_measure, Math.min(measureCount, Number(event.target.value) || section.start_measure)) })} /></label>
      <label className="section-label-input">Label<input type="text" maxLength={40} value={section.label} onChange={event => updateSection(index, { label: event.target.value })} /></label>
      <label className="repeat-toggle"><input type="checkbox" checked={section.repeat} onChange={event => updateSection(index, { repeat: event.target.checked })} />Repeat</label>
      <button className="icon-button" aria-label={`Remove ${section.label} section`} onClick={() => setOptions(current => ({ ...current, sections: current.sections.filter((_, row) => row !== index) }))}><Trash2 size={15} /></button>
    </div>)}</div>}
    <div className="notation-controls-footer"><p>Uses your saved hit list and rhythm grid. Audio transcription is not rerun.</p><button className="primary-button" onClick={() => void save()} disabled={saving}>{saving ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}{saving ? 'Rebuilding score...' : 'Apply notation & rebuild'}</button></div>
    {error && <p className="notation-error" role="alert">{error}</p>}
  </section>;
}
