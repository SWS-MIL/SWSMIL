import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models

from modules.mamba.mamba_ssm import Mamba
def initialize_weights(module):
    for m in module.modules():
        if isinstance(m,nn.Linear):
            # ref from clam
            nn.init.xavier_normal_(m.weight)
            if m.bias is not None:
                m.bias.data.zero_()
        elif isinstance(m,nn.LayerNorm):
            nn.init.constant_(m.bias, 0)
            nn.init.constant_(m.weight, 1.0)

class Resnet(nn.Module):
    def __init__(self):
        super(Resnet, self).__init__()

        self.model = list(models.resnet50(pretrained = True).children())[:-1]
        self.features = nn.Sequential(*self.model)

        self.feature_extractor_part2 = nn.Sequential(
            nn.Linear(2048, 4096),
            nn.ReLU(),
            nn.Dropout(p=0.25),
            nn.Linear(4096, 512),
            nn.ReLU(),
            nn.Dropout(p=0.25)
        )
        self.classifier = nn.Linear(512,1)
        initialize_weights(self.feature_extractor_part2)
        initialize_weights(self.classifier)
    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x=self.feature_extractor_part2(x)
        # feat = torch.mean(x,dim=0)
        x1 = self.classifier(x)
        # x2 = torch.mean(x1, dim=0).view(1,-1)
        x2,_ = torch.max(x1, dim=0)
        x2=x2.view(1,-1)
        return x2,x
class AttentionGated(nn.Module):
    def __init__(self,n_classes,input_dim=512,act='relu',bias=False,dropout=False):
        super(AttentionGated, self).__init__()
        self.L = 512
        self.D = 128 #128
        self.K = 1

        self.feature = [nn.Linear(1024, 512)]
        self.feature += [nn.ReLU()]
        self.feature += [nn.Dropout(0.25)]
        self.feature = nn.Sequential(*self.feature)

        self.classifier = nn.Sequential(
            nn.Linear(self.L*self.K, n_classes),
        )

        self.attention_a = [
            nn.Linear(self.L, self.D,bias=bias),
        ]
        if act == 'gelu': 
            self.attention_a += [nn.GELU()]
        elif act == 'relu':
            self.attention_a += [nn.ReLU()]
        elif act == 'tanh':
            self.attention_a += [nn.Tanh()]

        self.attention_b = [nn.Linear(self.L, self.D,bias=bias),
                            nn.Sigmoid()]

        if dropout:
            self.attention_a += [nn.Dropout(0.25)]
            self.attention_b += [nn.Dropout(0.25)]

        self.attention_a = nn.Sequential(*self.attention_a)
        self.attention_b = nn.Sequential(*self.attention_b)

        self.attention_c = nn.Linear(self.D, self.K,bias=bias)

        self.apply(initialize_weights)
    def forward(self, x):
        x = self.feature(x.squeeze(0))

        a = self.attention_a(x)
        b = self.attention_b(x)
        A = a.mul(b)
        A = self.attention_c(A)

        A = torch.transpose(A, -1, -2)  # KxN
        A = F.softmax(A, dim=-1)  # softmax over N
        x = torch.matmul(A,x)

        Y_prob = self.classifier(x)

        return Y_prob

class CIRCLE_MAMBA_MIL(nn.Module):
    def __init__(self,n_classes,dropout,act):
        super(CIRCLE_MAMBA_MIL, self).__init__()
        self.L = 512 #512
        self.D = 128 #128
        self.K = 1
        self.feature = [nn.Linear(2048, 512)]
        self.mamba_block = Mamba(d_model=1024, d_state=128, d_conv=4, expand=2)
        if act.lower() == 'gelu':
            self.feature += [nn.GELU()]
        else:
            self.feature += [nn.ReLU()]

        if dropout:
            self.feature += [nn.Dropout(0.25)]

        self.feature = nn.Sequential(*self.feature)

        self.attention = nn.Sequential(
            nn.Linear(self.L, self.D),
            nn.Tanh(),
            nn.Linear(self.D, self.K)
        )
        self.classifier = nn.Sequential(
            nn.Linear(self.L*self.K, n_classes),
        )

        self.apply(initialize_weights)
    def forward(self, x, return_attn=False,no_norm=False):
        x_reverse = torch.flip(x, [1])
        x_mamba = self.mamba_block(x)
        x_reverse_mamba = torch.flip(x_mamba, [1])
        x_reverse_mamba_reverse = torch.flip(x_reverse_mamba, [1])
        x_total = torch.cat([x_mamba,x_reverse_mamba_reverse],dim=2)
        # print(x_total.shape)
        feature = self.feature(x_total)
        
        # feature = group_shuffle(feature)
        feature = feature.squeeze(0)
        A = self.attention(feature)
        A_ori = A.clone()
        A = torch.transpose(A, -1, -2)  # KxN
        A = F.softmax(A, dim=-1)  # softmax over N
        M = torch.mm(A, feature)  # KxL
        Y_prob = self.classifier(M)

        if return_attn:
            if no_norm:
                return Y_prob,A_ori
            else:
                return Y_prob,A
        else:
            return Y_prob

