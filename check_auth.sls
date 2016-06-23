{% set minion = salt['pillar.get']('target_server') %}

check_minion_allowed_runner:
  salt.runner:
    - name: authminion.servicenow
    - minion_id: {{ minion }}
