{% set minion_id = data.get('id', {}) %}
{% set act = data.get('act', {}) %}

{% if act == 'pend' %}
check_minion_auth:
  runner.state.orchestrate:
    - mods: orchestrate.check_auth
    - pillar: { target_server: {{ minion_id }} }
{% endif %}
