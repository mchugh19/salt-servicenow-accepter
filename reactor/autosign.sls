{% set minion_id = data.get('minion_id', {}) %}
minion_add:
  wheel.key.accept:
    - match: {{ minion_id }}
  local.state.apply:
    - tgt: 'salt-master'
    - arg:
      - slack.autosign
    - kwarg:
        pillar:
          minion: {{ minion_id }}
