"""Data-analysis add-on behavior in isolated template copies."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/tests'))
from harness import HarnessTestCase  # noqa: E402


class DataAddonTests(HarnessTestCase):
    data_addon = True

    def analyzer_pass(self, phase):
        self.put('analysis.md', 'Findings: data fits the request. Limitations: synthetic example only.\n')
        self.run_cli('analysis-report', 'T001', '--phase', phase, '--verdict', 'pass', '--file', 'analysis.md')

    def test_empty_data_automatically_skips_analyzer(self):
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001')
        self.assertEqual(self.task()['status'], 'building')
        self.assertEqual(self.task()['analysis_state']['pre']['status'], 'skipped')
        self.assertEqual(self.task()['analyses'], [])
        self.assertEqual(self.task()['inspections'], [])

    def test_pre_and_post_analysis_complete_without_exposing_records(self):
        original = 'id,value\n1,PRIVATE_VALUE_42\n'
        self.put('data/raw/input.csv', original)
        self.configure(); self.add(data='auto')
        self.run_cli('start', 'T001', success=False)
        self.assertEqual(self.task()['status'], 'analyzing')
        packet = self.run_cli('context', 'T001', '--role', 'analyzer').stdout
        self.assertNotIn('PRIVATE_VALUE_42', packet)
        self.assertIn('metadata-only', packet)
        self.analyzer_pass('pre')
        self.put('data/processed/output.json', [{'id': 1, 'total': 2}])
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        self.assertEqual(self.task()['analysis_pending']['phase'], 'post')
        self.analyzer_pass('post'); self.handoff('reviewer')
        self.review()
        self.handoff('tester'); self.finish(); self.run_cli('check')
        self.assertEqual((self.root / 'data/raw/input.csv').read_text(), original)
        self.assertEqual(len(self.task()['analyses']), 2)
        self.put('data/raw/input.csv', 'id,value\n2,newer_dataset\n')
        self.run_cli('check')  # Saved historical reports remain valid.

    def test_expected_missing_data_asks_and_does_not_block_other_tasks(self):
        self.configure(); self.add(data='expected')
        result = self.run_cli('start', 'T001', success=False)
        self.assertIn('User question:', result.stdout)
        self.assertEqual(self.task()['status'], 'awaiting-data')
        other = self.add(); self.run_cli('start', other)
        self.put('data/raw/input.csv', 'id\n1\n')
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        self.assertEqual(self.task()['status'], 'analyzing')
        self.analyzer_pass('pre')
        self.assertEqual(self.task()['status'], 'building')

    def test_schema_mismatch_asks_for_correct_data(self):
        spec = json.loads((self.root / 'addons/data_analysis/templates/dataset.json').read_text())
        spec.update(path='data/raw/input.csv', required_columns=['required_id'])
        self.config['analysis']['datasets'] = [spec]
        self.put('config.json', self.config)
        self.put('data/raw/input.csv', 'wrong_column\nvalue\n')
        self.configure(); self.add(data='auto')
        result = self.run_cli('start', 'T001', success=False)
        self.assertIn('required columns missing', result.stdout)
        self.assertEqual(self.task()['analyses'], [])
        self.assertEqual(self.task()['status'], 'awaiting-data')

    def test_semantic_mismatch_requires_a_user_response(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.put('analysis.md', 'The file appears unrelated to the requested sales data.')
        self.run_cli('analysis-report', 'T001', '--phase', 'pre', '--verdict', 'needs-input',
                     '--file', 'analysis.md', '--question', 'Is this the correct sales dataset?')
        self.assertEqual(self.task()['status'], 'awaiting-data')
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        self.assertEqual(self.task()['status'], 'awaiting-data')
        self.put('reply.md', 'Yes, use this dataset; the identifiers represent sales.')
        self.run_cli('data-resolve', 'T001', '--file', 'reply.md', success=False)
        self.run_cli('data-resolve', 'T001', '--file', 'reply.md', '--approved', success=False)
        self.assertEqual(self.task()['status'], 'analyzing')
        self.analyzer_pass('pre')
        self.assertEqual(self.task()['status'], 'building')

    def test_changed_data_requires_new_analysis_and_review(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.analyzer_pass('pre'); self.handoff('reviewer')
        self.review(); self.handoff('tester')
        self.put('data/raw/input.csv', 'id\n2\n')
        self.run_cli('test', 'T001', success=False)
        self.assertEqual(self.task()['status'], 'analyzing')
        self.assertFalse(self.task()['review_pass'])
        self.analyzer_pass('pre')
        self.assertEqual(self.task()['status'], 'building')

    def test_changed_data_rejects_in_progress_analysis_report(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.put('data/raw/input.csv', 'id\n2\n')
        self.put('analysis.md', 'Stale findings')
        self.run_cli('analysis-report', 'T001', '--phase', 'pre', '--verdict', 'pass',
                     '--file', 'analysis.md', success=False)
        self.assertEqual(self.task()['analyses'], [])
        self.analyzer_pass('pre')

    def test_user_can_explicitly_revise_data_scope(self):
        self.configure(); self.add(data='expected'); self.run_cli('start', 'T001', success=False)
        self.put('reply.md', 'Approved scope change: this task does not need data.')
        self.run_cli('data-resolve', 'T001', '--file', 'reply.md', '--approved', '--data', 'none')
        self.assertEqual(self.task()['status'], 'building')
        self.assertEqual(self.task()['data_mode'], 'none')
        self.assertEqual(self.task()['analysis_state']['pre']['status'], 'skipped')

    def test_unsupported_and_empty_data_do_not_launch_analyzer(self):
        self.put('data/raw/input.bin', 'binary format requires a custom extractor')
        self.put('data/raw/empty.txt', '')
        self.configure(); self.add(data='auto')
        self.run_cli('start', 'T001', success=False)
        self.assertEqual(self.task()['status'], 'awaiting-data')
        saved = (self.root / self.task()['inspections'][-1]).read_text()
        self.assertIn('unsupported format', saved)
        self.assertIn('empty content', saved)
        self.assertEqual(self.task()['analyses'], [])

    def test_generated_data_can_use_post_only_analysis(self):
        self.configure(); self.add(data='expected', phases=['post'])
        self.run_cli('start', 'T001')
        self.put('data/generated/output.jsonl', '{"id":1}\n')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        self.analyzer_pass('post'); self.handoff('reviewer')
        self.assertNotIn('pre', self.task()['analysis_state'])

    def test_analysis_limits_and_bad_settings_are_explicit(self):
        self.config['analysis']['max_file_bytes'] = 3
        self.put('config.json', self.config)
        self.put('data/raw/input.txt', 'too large')
        self.configure(); self.add(data='auto')
        result = self.run_cli('start', 'T001', success=False)
        self.assertIn('size limit', result.stdout)
        self.assertEqual(self.task()['status'], 'awaiting-data')

    def provenance_entry(self, path, **overrides):
        entry = json.loads((self.root / 'addons/data_analysis/templates/provenance.json').read_text())
        entry.update(path=path, **overrides)
        return entry

    def test_inspection_preserves_declared_source_and_locator(self):
        entry = self.provenance_entry('data/raw/input.csv', source='Publisher export 2026-10',
                                      locator='sheet Sales, rows 2-5, field id')
        self.config['analysis']['provenance'] = [entry]
        self.put('config.json', self.config)
        self.put('data/raw/input.csv', 'id\nPRIVATE_ROW_VALUE\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        record = json.loads((self.root / self.task()['inspections'][-1]).read_text())
        provenance = record['inspection']['files'][0]['provenance']
        self.assertEqual(provenance['source'], entry['source'])
        self.assertEqual(provenance['locator'], entry['locator'])
        self.assertEqual(provenance['attribution'], 'provided; not independently verified')
        self.assertNotIn('PRIVATE_ROW_VALUE', self.run_cli('context', 'T001', '--role', 'analyzer').stdout)

    def test_unknown_origins_explicit_even_in_generated_folder(self):
        self.put('data/generated/input.json', [{'id': 1}])
        self.configure(); self.add(data='auto', phases=['post']); self.run_cli('start', 'T001')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        record = json.loads((self.root / self.task()['inspections'][-1]).read_text())
        provenance = record['inspection']['files'][0]['provenance']
        self.assertEqual(provenance['kind'], 'unknown')
        self.assertEqual(provenance['source'], 'source unavailable')
        self.assertEqual(provenance['locator'], 'source unavailable')
        self.assertNotEqual(provenance['attribution'], 'exempt')

    def test_original_generated_content_has_explicit_exemption(self):
        self.config['analysis']['provenance'] = [self.provenance_entry('data/generated/input.json', kind='generated')]
        self.put('config.json', self.config); self.put('data/generated/input.json', [{'id': 1}])
        self.configure(); self.add(data='auto', phases=['post']); self.run_cli('start', 'T001')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        record = json.loads((self.root / self.task()['inspections'][-1]).read_text())
        provenance = record['inspection']['files'][0]['provenance']
        self.assertEqual(provenance['attribution'], 'exempt')
        self.assertIn('not required', provenance['source'])

    def test_derived_outputs_retain_original_source_lineage(self):
        raw = self.provenance_entry('data/raw/input.csv', source='Original exported ledger', locator='row 2')
        derived = self.provenance_entry('data/processed/output.json', kind='derived',
                                        derived_from=['data/raw/input.csv'])
        self.config['analysis']['provenance'] = [raw, derived]
        self.put('config.json', self.config); self.put('data/raw/input.csv', 'id\n1\n')
        self.put('data/processed/output.json', [{'id': 1}])
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.analyzer_pass('pre')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        record = json.loads((self.root / self.task()['inspections'][-1]).read_text())
        lineage = record['inspection']['files'][0]['provenance']['lineage']
        self.assertEqual(lineage[0]['source'], raw['source'])
        self.assertEqual(lineage[0]['locator'], 'row 2')

    def test_provenance_rejects_external_paths_cycles_and_false_exemptions(self):
        cases = [
            [self.provenance_entry('outside.csv')],
            [self.provenance_entry('data/raw/a.csv', kind='derived', derived_from=['../outside.csv'])],
            [self.provenance_entry('data/raw/a.csv', kind='derived', derived_from=['data/raw/b.csv']),
             self.provenance_entry('data/raw/b.csv', kind='derived', derived_from=['data/raw/a.csv'])],
            [self.provenance_entry('data/generated/a.json', kind='generated', derived_from=['data/raw/a.csv'])],
        ]
        for declarations in cases:
            with self.subTest(declarations=declarations):
                self.config['analysis']['provenance'] = declarations
                self.put('config.json', self.config)
                self.run_cli('configure', '--config', 'config.json', '--approved', success=False)
                self.assertFalse((self.root / '.workflow/config.json').exists())

    def test_provenance_changes_require_reassessment(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        c = json.loads((self.root / '.workflow/config.json').read_text())
        c['analysis']['provenance'] = [self.provenance_entry('data/raw/input.csv', source='Newly supplied original source')]
        self.put('.workflow/config.json', c)  # Simulate an explicitly reviewed configuration update.
        self.put('analysis.md', 'Stale origin details')
        self.run_cli('analysis-report', 'T001', '--phase', 'pre', '--verdict', 'pass', '--file', 'analysis.md', success=False)
        self.assertEqual(self.task()['analyses'], [])
        record = json.loads((self.root / self.task()['inspections'][-1]).read_text())
        self.assertEqual(record['inspection']['files'][0]['provenance']['source'], 'Newly supplied original source')

    def test_equivalent_dataset_paths_keep_schema_requirements(self):
        spec = json.loads((self.root / 'addons/data_analysis/templates/dataset.json').read_text())
        spec.update(path='data/raw/input.csv', required_columns=['required_id'])
        self.config['analysis']['datasets'] = [spec]
        self.put('config.json', self.config); self.put('data/raw/input.csv', 'wrong_column\n1\n')
        self.configure()
        self.run_cli('add', '--title', 'Analyze', '--path', 'example.py', '--criterion', 'Schema valid', '--prompt', 'prompt.md',
                     '--data', 'expected', '--dataset', './data/raw/input.csv')
        self.run_cli('start', 'T001', success=False)
        self.assertEqual(self.task()['datasets'], ['data/raw/input.csv'])
        self.assertEqual(self.task()['status'], 'awaiting-data')
        self.assertIn('required columns missing', self.task()['data_question'])

    def test_explicit_dataset_phase_conflict_rejected_at_intake(self):
        self.configure()
        self.run_cli('add', '--title', 'Analyze', '--path', 'example.py', '--criterion', 'Valid', '--prompt', 'prompt.md',
                     '--data', 'expected', '--dataset', 'data/generated/output.csv',
                     '--analysis-phase', 'pre', success=False)
        self.assertEqual(json.loads((self.root / '.workflow/state.json').read_text())['tasks'], [])

    def test_disappeared_input_requires_user_question_not_skip(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.analyzer_pass('pre'); (self.root / 'data/raw/input.csv').unlink()
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        self.assertEqual(self.task()['status'], 'awaiting-data')
        self.assertIn('missing', self.task()['data_question'])
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        self.assertEqual(self.task()['status'], 'awaiting-data')

    def test_pending_inspection_staleness_reported_before_model_work(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.put('data/raw/input.csv', 'id\n2\n')
        packet = json.loads(self.run_cli('context', 'T001', '--role', 'analyzer').stdout)
        self.assertTrue(packet['analysis_stale'])
        self.run_cli('check', success=False)
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        packet = json.loads(self.run_cli('context', 'T001', '--role', 'analyzer').stdout)
        self.assertFalse(packet['analysis_stale'])

    def test_context_prefers_inspection_over_generic_configuration(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        result = self.run_cli('context', 'T001', '--role', 'analyzer', '--max-chars', '3500')
        packet = json.loads(result.stdout)
        self.assertIn('inspections', packet)
        self.assertEqual(packet['active_analysis']['phase'], 'pre')
        self.assertNotIn('validation', packet)
        self.assertLessEqual(len(result.stdout.strip()), 3500)

    def test_ambiguous_json_does_not_reach_analyzer(self):
        self.configure()
        for index, raw in enumerate(('[{"id":1,"id":2}]', '[{"id":NaN}]', '[{"id":Infinity}]', '[{"id":1e999}]'), 1):
            name = f'data/raw/input{index}.json'
            self.put(name, raw)
            task = self.run_cli('add', '--title', 'Analyze', '--path', 'example.py', '--criterion', 'Valid JSON', '--prompt', 'prompt.md',
                                '--dataset', name).stdout.strip()
            self.run_cli('start', task, success=False)
        tasks = json.loads((self.root / '.workflow/state.json').read_text())['tasks']
        self.assertTrue(all(task['status'] == 'awaiting-data' and not task['analyses'] for task in tasks))

    def test_optional_column_contracts_enforced_without_rewriting(self):
        spec = json.loads((self.root / 'addons/data_analysis/templates/dataset.json').read_text())
        spec.update(path='data/raw/input.csv', column_rules={'id': {'type':'integer','nullable':False,'unique':True},
                    'amount': {'type':'number','minimum':0}, 'day': {'type':'date'}})
        self.config['analysis']['datasets'] = [spec]
        self.put('config.json', self.config)
        original = 'id,amount,day\n1,-5,2026-10-25\n1,2,not-a-date\n'
        self.put('data/raw/input.csv', original)
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.assertEqual(self.task()['status'], 'awaiting-data')
        self.assertEqual((self.root / 'data/raw/input.csv').read_text(), original)
        self.put('data/raw/input.csv', 'id,amount,day\n1,5,2026-10-25\n2,2,2026-10-26\n')
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        self.assertEqual(self.task()['status'], 'analyzing')

    def test_upstream_version_changes_invalidate_selected_derived_data(self):
        self.config['analysis']['provenance'] = [self.provenance_entry('data/processed/output.json', kind='derived',
                                                                 derived_from=['data/raw/input.csv'])]
        self.put('config.json', self.config)
        self.put('data/raw/input.csv', 'id\n1\n'); self.put('data/processed/output.json', [{'id':1}])
        self.configure(); self.add(data='auto', phases=['post']); self.run_cli('start', 'T001')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        old = (self.root / self.task()['inspections'][-1]).read_text()
        self.put('data/raw/input.csv', 'id\n2\n')
        packet = json.loads(self.run_cli('context', 'T001', '--role', 'analyzer').stdout)
        self.assertTrue(packet['analysis_stale'])
        self.run_cli('data-check', 'T001', '--phase', 'post', success=False)
        new = (self.root / self.task()['inspections'][-1]).read_text()
        self.assertNotEqual(old, new)
        self.assertIn('observed current parent', new)

    def enable_profiles(self):
        self.config['analysis']['data_access'] = 'approved-summaries'
        self.config['analysis']['profiling']['enabled'] = True
        self.put('config.json', self.config)

    def test_profile_aggregate_contract_and_default_off(self):
        self.put('data/raw/input.csv', 'amount,name\n1,PRIVATE_NAME_42\n3,OTHER_NAME\n3,OTHER_NAME\n')
        self.enable_profiles(); self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.run_cli('profile', 'T001', '--phase', 'pre')
        record = json.loads((self.root / self.task()['profiles'][-1]).read_text())
        summary = record['summary']['files'][0]
        self.assertEqual(summary['duplicate_records'], 1)
        self.assertEqual(summary['columns']['amount']['numeric']['mean'], 7/3)
        packet = self.run_cli('context', 'T001', '--role', 'analyzer').stdout
        self.assertIn('"profile"', packet)
        self.assertNotIn('PRIVATE_NAME_42', packet)

    def test_profile_requires_explicit_approved_access(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.run_cli('profile', 'T001', '--phase', 'pre', success=False)
        self.assertEqual(self.task()['profiles'], [])

    def test_profile_not_exposed_after_access_restriction(self):
        self.put('data/raw/input.csv', 'amount\n1\n3\n')
        self.enable_profiles(); self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.run_cli('profile', 'T001', '--phase', 'pre')
        self.config['analysis']['profiling']['enabled'] = False
        self.config['analysis']['data_access'] = 'metadata-only'
        self.put('candidate.json', self.config)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', '--preview-hash', preview['preview_hash'])
        packet = json.loads(self.run_cli('context', 'T001', '--role', 'analyzer').stdout)
        self.assertNotIn('profile', packet)
        self.assertTrue(self.task()['profiles'])  # History preserved without injecting summaries.

    def test_config_update_preserves_unanswered_semantic_question(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        self.put('analysis.md', 'Dataset meaning is unclear.')
        self.run_cli('analysis-report', 'T001', '--phase', 'pre', '--verdict', 'needs-input',
                     '--file', 'analysis.md', '--question', 'What does the id represent?')
        self.config['context']['max_chars'] = 9000
        self.put('candidate.json', self.config)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', '--preview-hash', preview['preview_hash'])
        self.assertEqual(self.task()['status'], 'awaiting-data')
        self.assertEqual(self.task()['data_question'], 'What does the id represent?')
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        self.put('reply.md', 'The id is a sales identifier; use this dataset.')
        self.run_cli('data-resolve', 'T001', '--file', 'reply.md', '--approved', success=False)
        self.assertEqual(self.task()['status'], 'analyzing')


    def test_enabled_addon_extends_guidance_and_roles(self):
        self.configure()
        self.assertIn('## Data-analysis add-on', (self.root / 'AGENTS.md').read_text())
        self.assertIn('name: analyzer', (self.root / '.claude/agents/analyzer.md').read_text())

    def test_disabling_addon_clears_active_data_scope(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto'); self.run_cli('start', 'T001', success=False)
        candidate = json.loads(json.dumps(self.config))
        candidate['addons']['data_analysis'] = False
        del candidate['analysis']
        self.put('candidate.json', candidate)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', '--preview-hash', preview['preview_hash'])
        task = self.task()
        self.assertEqual((task['data_mode'], task['analysis_phases'], task['analysis_state']), ('none', [], {}))
        self.assertNotIn('Data-analysis add-on', (self.root / 'AGENTS.md').read_text())
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)


    def add_source(self, fail=False, parser=True):
        external = self.root.parent / 'external.csv'
        external.write_text('id,amount\n1,5\n2,7\n')
        self.put('scripts/fetch_demo.py', 'import os, shutil, sys\n'
                 + ('sys.exit(3)\n' if fail else '')
                 + 'os.makedirs(os.environ["FETCH_TARGET"] + "/part", exist_ok=True)\n'
                 'shutil.copy(sys.argv[1], os.environ["FETCH_TARGET"] + "/part/items.csv")\n')
        self.put('scripts/parse_demo.py', 'import csv, json, os, pathlib\n'
                 'rows = list(csv.DictReader(open(os.environ["FETCH_DIR"] + "/part/items.csv")))\n'
                 'pathlib.Path(os.environ["PARSE_TARGET"], "items.json").write_text(json.dumps(rows))\n')
        spec = json.loads((self.root / 'addons/data_analysis/templates/source.json').read_text())
        spec.update(name='demo', origin='Demo vendor export', locator='items endpoint',
                    command=['python3', 'scripts/fetch_demo.py', str(external)], target='data/raw/demo',
                    parser=['python3', 'scripts/parse_demo.py'] if parser else [],
                    parsed_target='data/processed/demo' if parser else '', timeout_seconds=30)
        self.config['analysis']['sources'] = [spec]
        self.put('config.json', self.config)

    def test_fetch_publishes_versioned_raw_and_parsed_data_with_provenance(self):
        self.add_source(); self.configure()
        self.add(data='expected', paths=('scripts/fetch_demo.py', 'scripts/parse_demo.py'))
        self.run_cli('start', 'T001', success=False)  # No data yet: asks.
        self.assertEqual(self.task()['status'], 'awaiting-data')
        self.run_cli('fetch', 'T001', '--source', 'demo')
        record = json.loads((self.root / self.task()['fetches'][-1]).read_text())
        raw, parsed = record['raw'][0]['path'], record['parsed'][0]['path']
        self.assertRegex(raw, r'^data/raw/demo/\d{8}T\d{6}Z/part/items.csv$')
        self.assertRegex(parsed, r'^data/processed/demo/\d{8}T\d{6}Z/items.json$')
        self.assertEqual(record['agent'], 'claude')
        self.assertFalse((self.root / '.workflow/fetch-staging').exists())
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        self.assertEqual(self.task()['status'], 'analyzing')
        inspection = json.loads((self.root / self.task()['inspections'][-1]).read_text())['inspection']
        provenance = inspection['files'][0]['provenance']
        self.assertEqual((provenance['source'], provenance['locator']), ('Demo vendor export', 'items endpoint'))
        packet = json.loads(self.run_cli('context', 'T001', '--role', 'fetcher').stdout)
        self.assertEqual(packet['sources'][0]['name'], 'demo')
        self.assertTrue((self.root / '.claude/agents/fetcher.md').is_file())

    def test_failed_fetch_publishes_nothing_and_unregistered_sources_are_refused(self):
        self.add_source(fail=True); self.configure(); self.add(data='expected', paths=('scripts/**',))
        self.run_cli('start', 'T001', success=False)
        self.assertIn('nothing was written', self.run_cli('fetch', 'T001', '--source', 'demo', success=False).stderr)
        self.assertFalse((self.root / 'data/raw/demo').exists())
        self.assertEqual(self.task()['fetches'], [])
        self.assertIn('Unknown source', self.run_cli('fetch', 'T001', '--source', 'other', success=False).stderr)
        self.run_cli('fetch', 'T001', '--source', 'demo', agent='codex', success=False)  # Not codex's claim.

    def test_source_definitions_are_validated(self):
        self.add_source()
        good = json.loads(json.dumps(self.config['analysis']['sources'][0]))
        for change in ({'command': ['bash', 'fetch.sh']}, {'target': 'data/processed/demo'},
                       {'target': 'outside/demo'}, {'parser': [], 'parsed_target': 'data/processed/demo'},
                       {'command': ['python3', 'scripts/missing.py']}):
            with self.subTest(change=change):
                self.config['analysis']['sources'] = [good | change]
                self.put('config.json', self.config)
                self.run_cli('validate-config', '--config', 'config.json', success=False)


    def test_format_and_provenance_errors_list_allowed_values(self):
        spec = json.loads((self.root / 'addons/data_analysis/templates/dataset.json').read_text())
        spec.update(path='data/raw/protocol.md', format='markdown', required_columns=[])
        self.config['analysis']['datasets'] = [spec]
        self.put('config.json', self.config)
        result = self.run_cli('validate-config', '--config', 'config.json', success=False)
        self.assertIn("unsupported format 'markdown'; use one of csv, json, jsonl, md, txt", result.stderr)
        spec['format'] = 'md'
        self.config['analysis']['provenance'] = [{'path': 'data/raw/*', 'source': 'x', 'locator': 'y', 'derived_from': []}]
        self.put('config.json', self.config)
        result = self.run_cli('validate-config', '--config', 'config.json', success=False)
        self.assertIn('one entry per exact file path', result.stderr)

    def test_passing_analysis_reports_resume_instead_of_restart(self):
        self.put('data/raw/input.csv', 'id\n1\n')
        self.configure(); self.add(data='auto')
        result = self.run_cli('start', 'T001', success=False)
        self.assertIn('do not run start again', result.stdout)
        result = self.run_cli('start', 'T001', success=False)
        self.assertIn('waiting for analysis-report', result.stderr)
        self.put('analysis.md', 'Findings: fits. Limitations: synthetic.\n')
        result = self.run_cli('analysis-report', 'T001', '--phase', 'pre', '--verdict', 'pass', '--file', 'analysis.md')
        self.assertIn('T001 resumed: building (claim: claude)', result.stdout)


if __name__ == '__main__':
    unittest.main()
