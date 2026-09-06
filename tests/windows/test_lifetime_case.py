"""Closure assembly must record callees automatically and refuse unresolved names."""
from pathlib import Path
import unittest
from lifetime_case import (SHADOWED, UnresolvedReference, build_case, definitions,
                           nonconforming_definitions)

SOURCE=Path(__file__).resolve().parents[1]/'test_codex_bridge_launcher.bats'
STUBS='''
_lifetime_bind_root() { :; }
_lifetime_begin() { :; }
_lifetime_end() { :; }
_windows_native_bridge_pids() { printf '11\\n22\\n'; }
taskkill() { return 5; }
_windows_native_wait_tasklist_gone() { return 0; }
_windows_native_assert_no_bridge_processes() { return 0; }
'''
ENTRY='_windows_native_reap_bridge_root'


class CaseTests(unittest.TestCase):
    def setUp(self): self.source=SOURCE.read_text()

    def test_closure_records_callees_without_a_hand_written_list(self):
        self.assertIn('_windows_native_diag_record_taskkill() {',build_case(self.source,ENTRY,STUBS))
        self.assertIn('_windows_native_diag_record_signal() {',
                      build_case(self.source,'_cleanup_windows_native_processes'))

    def test_command_substitution_call_sites_are_followed(self):
        # `root_key="$(_windows_native_diag_root_key "$root")"` is a call site. An
        # earlier prototype missed it and the unresolved gate passed a case that
        # then died at rc=127, so pin this shape directly.
        script=build_case(self.source,'_windows_native_diag_snapshot')
        self.assertIn('_windows_native_diag_root_key() {',script)

    def test_stubs_are_appended_after_the_closure_so_they_win(self):
        script=build_case(self.source,ENTRY,STUBS)
        real=script.index('_windows_native_bridge_pids() {')
        stub=script.index("_windows_native_bridge_pids() { printf '11")
        self.assertLess(real,stub)

    def test_undefined_call_is_rejected_at_generation_time(self):
        broken=self.source.replace('_windows_native_diag_record_taskkill "',
                                   '_qzzx_missing_probe "$pid"; _windows_native_diag_record_taskkill "',1)
        with self.assertRaises(UnresolvedReference) as raised: build_case(broken,ENTRY,STUBS)
        self.assertIn('_qzzx_missing_probe',str(raised.exception))

    def test_removed_helper_definition_is_rejected(self):
        removed=self.source.replace(definitions(self.source)['_windows_native_diag_append'],'',1)
        with self.assertRaises(UnresolvedReference) as raised: build_case(removed,ENTRY,STUBS)
        self.assertIn('_windows_native_diag_append',str(raised.exception))

    def test_missing_entry_is_rejected(self):
        with self.assertRaises(UnresolvedReference): build_case(self.source,'_qzzx_missing_entry',STUBS)

    def test_nonconforming_definition_is_rejected_before_assembly(self):
        # The HELPER scan only sees `_[a-z]...`. A helper named otherwise would be
        # invisible to it, so the definition itself is refused.
        offbook=self.source+'\nvkmr_offbook_helper() {\n  :\n}\n'
        with self.assertRaises(UnresolvedReference) as raised: build_case(offbook,ENTRY,STUBS)
        self.assertIn('vkmr_offbook_helper',str(raised.exception))

    def test_shadowed_names_stay_allowed(self):
        # setup/teardown are called by bats by name; taskkill shadows the external
        # command on purpose. None of them may trip the gate.
        self.assertEqual(set(),nonconforming_definitions(self.source))
        self.assertTrue(SHADOWED<=set(definitions(self.source))|{'setup','teardown','taskkill'})

    def test_undefined_call_without_the_prefix_is_still_not_detected(self):
        # Known hole, kept explicit: a non-conforming *call* is indistinguishable
        # from an external command without shell lexing, so it passes. The road to
        # it is closed by the test above, which refuses the definition.
        blind=self.source.replace('_windows_native_diag_record_taskkill "',
                                  'vkmr_offbook_helper "$pid"; _windows_native_diag_record_taskkill "',1)
        self.assertIn('vkmr_offbook_helper',build_case(blind,ENTRY,STUBS))

    def test_unparsable_case_is_rejected(self):
        with self.assertRaises(UnresolvedReference):
            build_case(self.source,ENTRY,STUBS,'\nif true; then\n')


if __name__=='__main__': unittest.main()
