"""One response envelope for current observations and explicitly synthetic replay."""


def overview_envelope(result, snapshots, *, freshness, catalysts, history=None, session_label='', replay=None):
    data = result.model_dump(mode='json')
    simulated = result.mode in ('demo', 'replay')
    coverage = snapshots[0].coverage.model_dump(mode='json') if snapshots else {
        'symbols_seen': 0, 'symbols_expected': 0, 'ratio': 0, 'venues': [], 'scope': 'covered_universe'}
    coverage['scope'] = 'configured_and_news_linked_observed_universe'
    coverage['measurement_definition'] = 'Coverage and shares refer to the covered universe, never the whole Korean market.'
    for key in ('premarket_hypothesis_top3', 'live_opening_flow_top3', 'entry_ready_top3'):
        items = data[key]
        status = 'DEMO' if simulated else result.quality if items else 'UNAVAILABLE'
        data[key] = {'status': status, 'items': items, 'explanation_codes': result.reason_codes}
    return {**data, 'version': '1.2', 'mode': 'demo' if simulated else 'live' if snapshots else 'unavailable',
            'as_of': result.as_of.isoformat(), 'live_trading': False,
            'data_source': 'synthetic_replay' if simulated else 'verified_observations_only',
            'session_label': session_label, 'provider': sorted({name for item in snapshots for name in item.provider}),
            'coverage': coverage, 'data_freshness': freshness, 'catalysts': catalysts,
            'rotation_history': history if history is not None else data['transitions'],
            'theme_flow_snapshots': [item.model_dump(mode='json') for item in snapshots],
            'replay': replay or {'available': True, 'session': '2026-10-12',
                'snapshot_times': ['08:55', '09:02', '09:05', '09:10', '09:20', '09:30']},
            'notice': ('DEMO · Invented inputs reproduce a type of opening rotation; they are not actual October 12 news or prices.'
                       if simulated else 'Turnover is observed trading activity, not investor net buying. Predictions and entries are separate.')}
