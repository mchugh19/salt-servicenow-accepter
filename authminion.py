import salt.client
import salt.config


def servicenow(minion_id):
  # Looking up hostname in servicenow. Ensure hostname and not fqdn
  minion_short = minion_id.split('.')[0]

  master_opts = salt.config.client_config('/etc/salt/master')
  master_name = master_opts['id'].split('_', 1)[0]
  client = salt.client.LocalClient(__opts__['conf_file'])
  output = client.cmd(master_name, 'servicenow.non_structured_query', ['cmdb_ci_server', 'name=' + minion_short]).values()
  try:
    for list in output:
      for first_dict in list:
        for key, value in first_dict.iteritems():
          if 'name' in key and minion_short.lower() in value.lower():
            __jid_event__.fire_event({
              'message': 'Authorizing minion: {0}'.format(minion_id),
              'minion_id': minion_id
            }, 'AcceptMinion')
            return True
  except: 
    __jid_event__.fire_event({'message': 'Unable to locate minion: {0}'.format(minion_id)}, 'RejectMinion')
    return False
