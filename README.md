# salt-servicenow-accepter

Hey everyone! I have been working to implement salt in an environment that did not previously use any sort of configuration management. This raises the problem of how to scale key handling as we expand our environment from proof of concept to thousands of nodes. I don’t want our distributed teams to have to wait for us to run a salt-key add command every time they try to use the system, and I also don’t want all hosts on our network auto-trusted by the master as that opens us up to a rogue machine being able to query our pillars and states (Currently we don’t have any privileged data accessible that way, but I’d rather things were set properly from the beginning). Here’s my quick solution to that problem. 

We use ServiceNow (SN) as our CMDB. While our data is likely to be less than 100% accurate, we at least have all of our server hostnames and FQDNs in the system. This should continue to be the case as CIs are autocreated by a VCAC workflow as part of our VM creation process. So all in all, we should have a good record of hostnames and can use them to determine if the new minion is a managed machine on our network.

Thus our desired workflow is:

<img src="https://cdn.rawgit.com/mchugh19/salt-servicenow-accepter/master/graphics/initial-workflow.svg" width="475">

New minion connects to salt-master -> salt master looks up minion name in SN CMDB -> if name exists, accept minion key

## Install ServiceNow Module
Luckily, there is already a salt module for communicating with the SN CMDB! Unfortunately, while it is in git, it did not make the current release of 2016.3.1. Fortunately, salt’s architecture is super modular making it trivial to add in that module. Just create a _modules directory in the salt states location (we are using gitfs, but normally it is /srv/salt/). Then add in [servicenow.py](https://github.com/saltstack/salt/blob/develop/salt/modules/servicenow.py) and tell the minions about the newly added module: 

```salt '*' saltutil.sync_modules```

After a few seconds, you should see that your servicenow module has been copied to the minions and is now available.

## Configure ServiceNow module
Now that we have the new module added to our hosts, we need to configure our ServiceNow credentials with a pillar:
```yaml
servicenow:
  instance_name: 'instancename' #http://instancename.service-now.com
  username: 'USERNAME'
  password: 'PASSWORD'
```

For this use-case we will only need our saltmaster to connect to SN, so we only need to make our pillar info available to that host in the pillar top file:
```yaml
'salt-master'
  - servicenow
```

## Query ServiceNow
Now that we have the servicenow module, we can query the CMDB with something like:

```salt 'salt-master' servicenow.non_structured_query cmdb_ci_server name=SOMEHOSTNAME```

If that runs successfully it should return the CMDB info for the specified server. If the server does not exist, it raises an exception and tells you the query was unsuccessful. With this, we now have the building blocks of our key acceptance system.

The workflow from above can now be expanded for the specifics. We want the salt-master to run a reactor when a new minion connects. If the new minion’s key is in a pending state (not yet accepted or rejected) we want to kick off an orchestrate job which starts a salt runner. 

<img src="https://cdn.rawgit.com/mchugh19/salt-servicenow-accepter/master/graphics/partial-workflow.svg" width="475">

New minion -> salt-master -> reactor -> if new minion key is pending -> orchestrate -> sn auth runner -> …

Let’s pause here to dive into why we would do it this way. 

## Reactor -> orchestrate -> runner… really?
Salt reactors are intended for quick filtering of events. Any reactor that needs time to run should not do the processing itself as that would block further reactors until finished. Instead you should run something else to handle that processing. In the case of doing a REST call to ServiceNow, should there be any network connection problems between our salt master and SN, it could take a while as we wait for the timeout, and we don’t want our reactor hanging all that time. Thus, we have our reactor kick an orchestrate job which puts the SN query into another thread freeing up our reactor. Our orchestrate job can now use our runner to perform the SN query. If it returns quickly, great! If not, no harm done. Now back to our workflow.

## SN Runner
We’ve got the servicenow module that can query our CMDB, but we aren’t just trying to do a query, we need use those results to determine if the host was actually found. We could extended our module to contain that logic, but as this isn’t necessarily an upstreamable change and can probably do better. Enter our custom runner. A salt runner is simply some python code that is run on the salt-master. Since we don’t want our not yet connected minion to somehow ask the CMDB if it is allowed to connect (we want the salt-master to handle it) this a perfect time for a salt runner. Here’s what I came up with after a few minutes (feel free to critique!).
```python
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
```

The first little bit looks up our salt-master so that we don’t have it hardcoded. It then kicks off running the servicenow module on the master to query for a hostname. If the host is found it fires off an event with an ‘AcceptMinion’ ID.


## Flush Out Workflow
Now that the heavy lifting logic is done, let’s finish out the workflow.

<img src="https://cdn.rawgit.com/mchugh19/salt-servicenow-accepter/master/graphics/complete-workflow.svg" width="475">

New minon -> salt-master -> reactor -> if new minion key is pending -> orchestrate -> sn auth runner -> AcceptMinion event -> reactor -> accept key

It is possible to have our custom runner perform the key accept itself, but personally I like the idea of it being more modular. We have just started using slack for team communications and when we accept the new minion’s key, we also send a message to the slack channel notifying everyone. I think the barrier to entry is a little bit higher on modifying some python in that-one-runner-that-one-guy-wrote-that-one-time, and it is likely a little bit easier to set standard stuff like slack messaging in a normal reactor.

## Putting it all together (TL;DR wrapup)

Let’s walk through our workflow:

#### New minon -> salt-master

On the salt event bus, this generates a 'salt/auth' event.

#### Reactor
On our salt master, we created a reactor config of
```yaml
'salt/auth':
  - /srv/salt/reactor/checkauth.sls
```

#### if new minion key is pending
Here’s the content of the checkauth reactor
```jinja
{% set minion_id = data.get('id', {}) %}
{% set act = data.get('act', {}) %}

{% if act == 'pend' %}
check_minion_auth:
  runner.state.orchestrate:
    - mods: orchestrate.check_auth
    - pillar: { target_server: {{ minion_id }} }
{% endif %}
```

#### orchestrate 
Set an orchestrate job to kick our custom runner
```jinja
{% set minion = salt['pillar.get']('target_server') %}

check_minion_allowed_runner:
  salt.runner:
    - name: authminion.servicenow
    - minion_id: {{ minion }}
```

#### sn auth runner
This custom runner can be placed in the salt state directory in a _runners directory. You then sync to your masters with:
```salt 'saltmaster' saltutil.sync_runners```

```python
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
```


#### AcceptMinion event -> reactor -> accept key
If our runner found the host in the CMDB it sent a AcceptMinion event. We can configure another salt reactor to listen for that event, and run our final reactor script to accept the key and message slack.
```
salt/run/*/AcceptMinion':
  - /srv/salt/reactor/autosign.sls
```

##### autosign.sls
```jinja
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
          minion: {{ minion_id }
```
_Note: The salt-master minion name is currently hardcoded here_

Now when we install a new salt-minion for a host that is in our environemnt, after a few seconds we get this message in our slack channel.

<img src="graphics/slack.png">

Mission accomplished!

