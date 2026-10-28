"""Unified SymbRoute Reasoning Pipeline combining Ledger Translation, Z3 Fast-Path, and Fallback."""
from pathlib import Path
import time

from .common import (cache_path, digest, input_hash, public_label, read_json,
                     sanitize, write_json)
from .z3_solver import OursZ3Solver
from .shared_translator import SharedTranslator
from .fallback_solver import SymbolicFallbackSolver


class SymbRoutePipeline:
    """
    Unified SymbRoute Neuro-Symbolic Pipeline:
    1. Shared Ledger Translation Engine (T_ours)
    2. Formal Z3 SMT Fast-Path (Checks SAT(P) & UNSAT(P & ¬Q))
    3. Symbolic CoT Fallback (Plan -> Execute -> Verify when Z3 is inconclusive)
    """
    def __init__(self, config):
        self.config = config
        self.translator = SharedTranslator(config)
        self.z3 = OursZ3Solver(config)
        self.clover = self.z3  # Backward-compatibility alias
        self.fallback = SymbolicFallbackSolver(config)
        self.symbcot = self.fallback  # Backward-compatibility alias

    def process_sample(self, row, run_dir=None, mode='hybrid', retry_errors=False):
        """
        Executes pipeline on a single problem sample.
        Args:
            row: dict containing 'index', 'example_id', 'premises', 'conclusion', 'label'
            run_dir: Path/str where intermediate translation caches are stored
            mode: 'hybrid' (Z3-first with SymbCoT fallback) | 'dual' (both) | 'clover_only' | 'symbcot_only'
            retry_errors: bool
        Returns:
            dict containing full trace, solver path, and prediction
        """
        fingerprint = digest(self.config)
        
        # 1. Shared Translation Stage
        if run_dir:
            cached = self.translator.translate_and_cache(row, run_dir, fingerprint, retry_error=retry_errors)
            if cached.get('status') != 'complete':
                return {
                    'index': row['index'],
                    'id': row['example_id'],
                    'gold_label': row.get('label'),
                    'status': 'error',
                    'error': cached.get('error', 'Translation failed'),
                    'solver_path': 'failed_translation'
                }
            translation_text = cached['translation']
            translation_source = cached['translation_source']
            translation_trace = cached.get('translation_trace')
        else:
            translation_text, translation_source, translation_trace = self.translator.translate(row)

        record = {
            'index': row['index'],
            'id': row['example_id'],
            'gold_label': row.get('label'),
            'translation': translation_text,
            'translation_source': translation_source,
            'mode': mode,
            'status': 'complete'
        }

        # 2. Execution by Mode
        if mode in ('z3_only', 'clover_only'):
            z3_res = self.z3.solve(translation_source)
            record.update({
                'predicted_label': z3_res['predicted_label'],
                'solver_path': 'z3_smt',
                'z3_details': z3_res,
                'clover_details': z3_res  # Backward-compatibility mirror
            })

        elif mode in ('fallback_only', 'symbcot_only'):
            fallback_res = self.fallback.solve(row, translation_text)
            record.update({
                'predicted_label': fallback_res['predicted_label'],
                'solver_path': 'symbolic_fallback',
                'fallback_details': fallback_res,
                'symbcot_details': fallback_res  # Backward-compatibility mirror
            })

        elif mode == 'dual':
            z3_res = self.z3.solve(translation_source)
            fallback_res = self.fallback.solve(row, translation_text)
            record.update({
                'z3_predicted': z3_res['predicted_label'],
                'z3_decisive': z3_res['is_decisive'],
                'z3_details': z3_res,
                'clover_predicted': z3_res['predicted_label'],
                'clover_decisive': z3_res['is_decisive'],
                'clover_details': z3_res,
                'fallback_predicted': fallback_res['predicted_label'],
                'fallback_details': fallback_res,
                'symbcot_predicted': fallback_res['predicted_label'],
                'symbcot_details': fallback_res,
                'predicted_label': z3_res['predicted_label'] if z3_res['is_decisive'] else fallback_res['predicted_label'],
                'solver_path': 'z3_smt' if z3_res['is_decisive'] else 'symbolic_fallback'
            })

        else:  # Default 'hybrid': Z3 Fast-Path with Symbolic Fallback
            started_z3 = time.monotonic()
            z3_res = self.z3.solve(translation_source)
            z3_elapsed = time.monotonic() - started_z3

            if z3_res['is_decisive']:
                # Decisive mathematical proof from Z3!
                record.update({
                    'predicted_label': z3_res['predicted_label'],
                    'solver_path': 'z3_smt',
                    'is_fallback': False,
                    'z3_details': z3_res,
                    'clover_details': z3_res,
                    'z3_seconds': round(z3_elapsed, 3)
                })
            else:
                # Inconclusive (Unknown / timeout) -> Fallback to Symbolic Reasoner
                started_cot = time.monotonic()
                fallback_res = self.fallback.solve(row, translation_text)
                cot_elapsed = time.monotonic() - started_cot
                
                record.update({
                    'predicted_label': fallback_res['predicted_label'],
                    'solver_path': 'symbolic_fallback',
                    'is_fallback': True,
                    'z3_details': z3_res,
                    'clover_details': z3_res,
                    'fallback_details': fallback_res,
                    'symbcot_details': fallback_res,
                    'z3_seconds': round(z3_elapsed, 3),
                    'cot_seconds': round(cot_elapsed, 3)
                })

        if 'label' in row and record.get('predicted_label'):
            record['correct'] = (record['predicted_label'] == row['label'])

        return record


# Backward-compatibility alias
OursHybridPipeline = SymbRoutePipeline
