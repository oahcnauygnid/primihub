from primihub.new_FL.algorithm.utils.net_work import MultiGrpcClients
from primihub.new_FL.algorithm.utils.base import BaseModel
from primihub.new_FL.algorithm.utils.file import check_directory_exist
from primihub.utils.logger_util import logger

import json
import numpy as np
from phe import paillier
from primihub.FL.model.metrics.metrics import fpr_tpr_merge2,\
                                              ks_from_fpr_tpr,\
                                              auc_from_fpr_tpr
from .base import PaillierFunc


class LogisticRegressionServer(BaseModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
    def run(self):
        if self.common_params['process'] == 'train':
            self.train()
    
    def train(self):
        # setup communication channels
        remote_parties = self.roles[self.role_params['others_role']]
        client_channel = MultiGrpcClients(local_party=self.role_params['self_name'],
                                          remote_parties=remote_parties,
                                          node_info=self.node_info,
                                          task_info=self.task_info)

        # server init
        method = self.common_params['method']
        if method == 'Plaintext' or method == 'DPSGD':
            server = Plaintext_DPSGD_Server(self.common_params['alpha'],
                                            client_channel)
        elif method == 'Paillier':
            server = Paillier_Server(self.common_params['alpha'],
                                     self.common_params['n_length'],
                                     client_channel)
        else:
            logger.error(f"Not supported method: {method}")

        # data preprocessing
        # minmaxscaler
        data_max = client_channel.recv_all('data_max')
        data_min = client_channel.recv_all('data_min')
        
        data_max = np.array(data_max).max(axis=0)
        data_min = np.array(data_min).min(axis=0)

        client_channel.send_all('data_max', data_max)
        client_channel.send_all('data_min', data_min)

        # server training
        logger.info("-------- start training --------")
        global_epoch = self.common_params['global_epoch']
        for i in range(global_epoch):
            logger.info(f"-------- global epoch {i+1} / {global_epoch} --------")
            server.train()
        
            # print metrics
            if self.common_params['print_metrics']:
                server.print_metrics()
        logger.info("-------- finish training --------")

        # receive final epsilons when using DPSGD
        if method == 'DPSGD':
            delta = self.common_params['delta']
            eps = client_channel.recv_all("eps")
            logger.info(f"For delta={delta}, the current epsilon is {max(eps)}")
        # send plaintext model when using Paillier
        elif method == 'Paillier':
            server.plaintext_server_model_broadcast()

        # receive final metrics
        trainMetrics = server.get_metrics()
        metric_path = self.common_params['metric_path']
        check_directory_exist(metric_path)
        logger.info(f"metric path: {metric_path}")
        with open(metric_path, 'w') as file_path:
            file_path.write(json.dumps(trainMetrics))


class Plaintext_DPSGD_Server:

    def __init__(self, alpha, client_channel):
        self.alpha = alpha
        self.client_channel = client_channel

        self.theta = None
        self.multiclass = None
        self.recv_output_dims()

        self.num_examples_weights = None
        if not self.multiclass:
            self.num_positive_examples_weights = None
            self.num_negtive_examples_weights = None
        self.recv_params()

    def recv_output_dims(self):
        # recv output dims for all clients
        Output_Dims = self.client_channel.recv_all('output_dim')

        # set final output dim
        output_dim = max(Output_Dims)
        if output_dim == 1:
            self.multiclass = False
        else:
            self.multiclass = True

        # send output dim to all clients
        self.client_channel.send_all("output_dim", output_dim)

    def recv_params(self):
        self.num_examples_weights = self.client_channel.recv_all('num_examples')
        
        if not self.multiclass:
            self.num_positive_examples_weights = \
                self.client_channel.recv_all('num_positive_examples')
            
            self.num_negtive_examples_weights = \
                    (np.array(self.num_examples_weights) - \
                    np.array(self.num_positive_examples_weights)).tolist()

    def client_model_aggregate(self):
        client_models = self.client_channel.recv_all("client_model")
        
        self.theta = np.average(client_models,
                                weights=self.num_examples_weights,
                                axis=0)

    def server_model_broadcast(self):
        self.client_channel.send_all("server_model", self.theta)

    def train(self):
        self.client_model_aggregate()
        self.server_model_broadcast()

    def get_loss(self):
        loss =  self.get_scalar_metrics('loss')
        if self.alpha > 0:
            loss += 0.5 * self.alpha * (self.theta ** 2).sum()
        return loss
    
    def get_scalar_metrics(self, metrics_name):
        metrics_name = metrics_name.lower()
        supported_metrics = ['loss', 'acc', 'auc']
        if metrics_name not in supported_metrics:
            logger.error(f"""Not supported metrics {metrics_name},
                          use {supported_metrics} instead""")

        client_metrics = self.client_channel.recv_all(metrics_name)
            
        return np.average(client_metrics,
                          weights=self.num_examples_weights)
    
    def get_fpr_tpr(self):
        client_fpr = self.client_channel.recv_all('fpr')
        client_tpr = self.client_channel.recv_all('tpr')
        client_thresholds = self.client_channel.recv_all('thresholds')

        # fpr & tpr
        # Note: fpr_tpr_merge2 only support two clients
        #       use ROC averaging when for multiple clients
        fpr,\
        tpr,\
        thresholds = fpr_tpr_merge2(client_fpr[0],
                                    client_tpr[0],
                                    client_thresholds[0],
                                    client_fpr[1],
                                    client_tpr[1],
                                    client_thresholds[1],
                                    self.num_positive_examples_weights,
                                    self.num_negtive_examples_weights)
        return fpr, tpr

    def get_metrics(self):
        server_metrics = {}

        loss = self.get_loss()
        server_metrics["train_loss"] = loss

        acc = self.get_scalar_metrics('acc')
        server_metrics["train_acc"] = acc
        
        if self.multiclass:
            auc = self.get_scalar_metrics('auc')
            server_metrics["train_auc"] = auc

            logger.info(f"loss={loss}, acc={acc}, auc={auc}")
        else:
            fpr, tpr = self.get_fpr_tpr()
            server_metrics["train_fpr"] = fpr
            server_metrics["train_tpr"] = tpr

            ks = ks_from_fpr_tpr(fpr, tpr)
            server_metrics["train_ks"] = ks

            auc = auc_from_fpr_tpr(fpr, tpr)
            server_metrics["train_auc"] = auc

            logger.info(f"loss={loss}, acc={acc}, ks={ks}, auc={auc}")

        return server_metrics
    
    def print_metrics(self):
        # print loss & acc
        loss = self.get_loss()
        acc = self.get_scalar_metrics('acc')
        if self.multiclass:
            auc = self.get_scalar_metrics('auc')
            logger.info(f"loss={loss}, acc={acc}, auc={auc}")
        else:
            logger.info(f"loss={loss}, acc={acc}")


class Paillier_Server(Plaintext_DPSGD_Server,
                      PaillierFunc):
    
    def __init__(self, alpha, n_length, client_channel):
        Plaintext_DPSGD_Server.__init__(self, alpha, client_channel)
        self.public_key,\
        self.private_key = paillier.generate_paillier_keypair(n_length=n_length) 
        self.public_key_broadcast()

    def public_key_broadcast(self):
        self.client_channel.send_all("public_key", self.public_key)

    def client_model_aggregate(self):
        client_models = self.client_channel.recv_all("client_model")

        self.theta = np.mean(client_models, axis=0)
        self.theta = np.array(self.encrypt_vector(self.decrypt_vector(self.theta)))

    def plaintext_server_model_broadcast(self):
        self.theta = np.array(self.decrypt_vector(self.theta))
        self.server_model_broadcast()

    def get_loss(self):
        return self.get_scalar_metrics('loss')

    def print_metrics(self):
        # print loss
        # pallier only support compute approximate loss
        client_loss = self.client_channel.recv_all('loss')
        # client loss is a ciphertext
        client_loss = self.decrypt_vector(client_loss)
        loss = np.average(client_loss,
                          weights=self.num_examples_weights)

        logger.info(f"loss={loss}")
