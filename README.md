# Fast Autoscaler

Fast Autoscaler is a General purpose, very fast cloud vm autoscaler

Compare to Cloud's built-in GCloud Managed Instances Group autoscaling and AWS Cloud Formation autoscaling. It's:
- Much faster:
  - Fast Autoscaler only time to create a new instance, usually less than 30 seconds
  - Clouds's built-in's autoscalers takes several minutes waiting
- Full control with config and API
- Precise
  - Fast Autoscaler creates exactly number of instance you need.
  - AWS Cloud Formation must be configured to create N instance a time. If you need less than N instances, more instances are wasted and have to shutdown. If you need more than N instances, you need to wait another 3-5 minutes to trigger another scaling event.
- Simple and straight forward control with config file and API
- Support major cloud providers, include those don't have a builtin autoscaler: GCloud, AWS, Azure and DigitalOcean
- (WIP) Easy to deploy: a docker container + a config file

# Usage

Before dockerfile (wip) is ready, it's a little bit complicated to deploy this autoscaler:
- Install gcloud cli, aws cli, digitalocean cli and azure cli and login. (Only need to install cloud providers in your config)
- Install pyenv, pipenv
Copy config.yml.example to config.yml and run.
```
pipenv sync
nohup pipenv run python app.py
```

# Config file

TODO, see config.yml.example for now

# API
```
POST /machines
body:
{
  group_name: string
  init_script?: string
}
response:
{
  machine_name: string
}

DELETE /machines/:machine_name

DELETE /machines/ip/:machine_ip

GET /machines
GET /machines/:machine_name
```