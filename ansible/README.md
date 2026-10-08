# Ansible

Документация: [docs/deploy_ansible.md](../docs/deploy_ansible.md).

Ручные гайды: [deploy_exit.md](../docs/deploy_exit.md), [deploy_transit.md](../docs/deploy_transit.md), [deploy_middle.md](../docs/deploy_middle.md).

```bash
cd ansible
cp inventory/hosts.yml.example inventory/hosts.yml
# пара: middle.hosts пустой; тройка: добавить vpn_middle
ansible-playbook -i inventory/hosts.yml playbooks/bootstrap.yml

# живую пару → тройку, когда из страны до exit уже нет SSH.
# На exit playbook заходит прыжком через middle. После успеха
# допишите ansible_ssh_common_args ProxyJump на vpn_exit (см. hosts.yml.example).
ansible-playbook -i inventory/hosts.yml playbooks/insert_middle.yml

# пара AmneziaWG 2.0 → middle и заново exit на 3.1, entry не трогать:
# docs/deploy_awg2_pair_middle.md
```
