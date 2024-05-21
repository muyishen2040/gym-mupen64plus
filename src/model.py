import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from torch.distributions import Normal



class ActorNet(nn.Module):
    def __init__(self, max_action, input_shape=(1, 84, 84), action_dim=5):
        super(ActorNet, self).__init__()
        self.max_action = max_action
        self.conv_layers = nn.Sequential(
            nn.Conv2d(input_shape[0], 32, kernel_size=8, stride=4),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=4, stride=2),
            nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size=3, stride=1),
            nn.ReLU(),
            nn.Flatten()
        )
        self.flattened_size = self._get_conv_output(input_shape)
        self.fc = nn.Linear(self.flattened_size, 512)
        self.mean = nn.Linear(512, action_dim)
        self.log_std = nn.Linear(512, action_dim)

    def _get_conv_output(self, shape):
        with torch.no_grad():
            input = torch.zeros(1, *shape)
            output = self.conv_layers(input)
            return int(np.prod(output.size()))

    def forward(self, state):
        x = self.conv_layers(state)
        x = F.relu(self.fc(x))
        mean = self.max_action*torch.tanh(self.mean(x))
        # mean = self.mean(x)
        log_std = self.log_std(x)
        log_std = torch.clamp(log_std, -20, 2)
        std = log_std.exp()
        return mean, std

class CriticNet(nn.Module):
    def __init__(self, input_shape=(1, 84, 84), action_dim=5):
        super(CriticNet, self).__init__()
        self.conv_layers_q1 = nn.Sequential(
            nn.Conv2d(input_shape[0], 32, kernel_size=8, stride=4),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=4, stride=2),
            nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size=3, stride=1),
            nn.ReLU(),
            nn.Flatten()
        )
        self.flattened_size = self._get_conv_output(input_shape)
        self.fc_q1 = nn.Sequential(
            nn.Linear(self.flattened_size + action_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
            nn.Linear(256, 1)
        )
        
        # self.output_q1 = nn.Linear(512, 1)

        self.conv_layers_q2 = nn.Sequential(
            nn.Conv2d(input_shape[0], 32, kernel_size=8, stride=4),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=4, stride=2),
            nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size=3, stride=1),
            nn.ReLU(),
            nn.Flatten()
        )
        self.fc_q2 = nn.Sequential(
            nn.Linear(self.flattened_size + action_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
            nn.Linear(256, 1)
        )
        # self.output_q2 = nn.Linear(512, 1)

    def _get_conv_output(self, shape):
        with torch.no_grad():
            input = torch.zeros(1, *shape)
            output = self.conv_layers_q1(input)
            return int(np.prod(output.size()))

    def forward(self, state, action):
        state_features_q1 = self.conv_layers_q1(state)
        q1 = torch.cat([state_features_q1, action], 1)
        # q1 = self.output_q1(F.relu(self.fc_q1(q1)))
        q1 = self.fc_q1(q1)

        state_features_q2 = self.conv_layers_q2(state)
        q2 = torch.cat([state_features_q2, action], 1)
        # q2 = self.output_q2(F.relu(self.fc_q2(q2)))
        q2 = self.fc_q2(q2)
        return q1, q2
    

class Actor:
    def __init__(self, device, actor_lr, min_action, max_action):
        self.device = device
        self.actor_lr = actor_lr
        self.min_action = min_action.to(device)
        self.max_action = max_action.to(device)
        self.actor_net = ActorNet(self.max_action).to(device)
        self.optimizer = torch.optim.Adam(self.actor_net.parameters(), lr=self.actor_lr)

    def choose_action(self, state):
        mean, std = self.actor_net(state)
        dist = torch.distributions.Normal(mean, std)
        action = dist.sample()
        action = torch.clamp(action, self.min_action, self.max_action)
        return action.detach().cpu().numpy()

    def evaluate(self, state):
        mean, std = self.actor_net(state)
        dist = torch.distributions.Normal(mean, std)
        noise = torch.distributions.Normal(0, 1)
        z = noise.sample()
        action = torch.tanh(mean + std * z)
        action = torch.clamp(action, self.min_action, self.max_action)
        action_logprob = dist.log_prob(mean + std * z) - torch.log(1 - action.pow(2) + 1e-6)

        return action, action_logprob, z, mean, std

    def learn(self, loss):
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()


class Critic:
    def __init__(self, device, critic_lr, tau):
        self.tau = tau
        self.critic_lr = critic_lr
        self.device = device
        self.critic_net = CriticNet().to(device)
        self.target_net = CriticNet().to(device)
        self.optimizer = torch.optim.Adam(self.critic_net.parameters(), lr=critic_lr, eps=1e-5)
        self.loss_func = nn.MSELoss()

    def update(self):
        for target_param, param in zip(self.target_net.parameters(), self.critic_net.parameters()):
            target_param.data.copy_(target_param.data * (1.0 - self.tau) + param.data * self.tau)

    def get_q_value(self, state, action):
        return self.critic_net(state, action)

    def get_target_q_value(self, state, action):
        return self.target_net(state, action)

    def learn(self, current_q1, current_q2, target_q):
        loss = self.loss_func(current_q1, target_q) + self.loss_func(current_q2, target_q)
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()


class Entropy:
    def __init__(self, device, entropy_lr, action_dim=5):
        self.entropy_lr = entropy_lr
        self.target_entropy = -action_dim
        self.log_alpha = torch.zeros(1, requires_grad=True, device=device)
        self.alpha = self.log_alpha.exp()
        self.optimizer = torch.optim.Adam([self.log_alpha], lr=entropy_lr)

    def learn(self, loss):
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()


# min_action = torch.tensor([-1.0, 0.0, 0.0])
# max_action = torch.tensor([1.0, 1.0, 1.0])
# device = 'cpu'

# actor = Actor(device, 0.1, min_action, max_action)

# import pdb
# pdb.set_trace()