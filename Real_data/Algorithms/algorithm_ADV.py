
import random
import IPython
import numpy as np
import pandas as pd
from tqdm import tqdm

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from DataLoader.dataloader import CustomDataset
import torch.optim as optim

from models import Classifier
from models import domain_Classifier
from DataLoader.dataloader import FairnessDataset

import time


from utils import cal_acc,cal_disparity




def Adversarial(dataset,dataset_name,alpha,clf, adv,optimizer_clf,optimizer_adv,lr_scheduler_clf,device, batch_size,n_epochs=200, seed=0):

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)



    training_tensors,validation_tensors, testing_tensors = dataset.get_dataset_in_tensor()
    X_train, Y_train, Z_train, XZ_train = training_tensors
    X_val, Y_val, Z_val, XZ_val = validation_tensors
    X_test, Y_test, Z_test, XZ_test = testing_tensors






    Y_test_co = Y_test.clone()
    Y_test_np = Y_test_co.cpu().detach().numpy()

    Z_test_co = Z_test.clone()
    Z_test_np = Z_test_co.cpu().detach().numpy()

    Y_val_np = Y_val.clone().detach().numpy()


    custom_dataset = CustomDataset(XZ_train, Y_train,Z_train)

    train_loader = torch.utils.data.DataLoader(
        custom_dataset, batch_size=batch_size,
        shuffle=True)
    loss_clf = torch.nn.BCELoss()

    loss_adv = torch.nn.BCELoss()


    with tqdm(range(n_epochs//4)) as epochs:
        epochs.set_description(f"Classifcation PreTraining Epoch: dataset: {dataset_name}, seed {seed}, adversary_loss_weight:{alpha}")
        for epoch in epochs:
            clf.train()
            for i, data in enumerate(train_loader):  # starting from the 0th batch
                # get the inputs and labels
                x, y, z = data
                x = x.to(device)
                y = y.unsqueeze(1).to(device)
                z = z.unsqueeze(1).to(device)
                optimizer_clf.zero_grad()
                classifier_output = clf(x)
                classifier_loss = loss_clf(classifier_output, y)  # compute loss
                classifier_loss.backward()
                optimizer_clf.step()
                epochs.set_postfix(loss=classifier_loss.item())
            lr_scheduler_clf.step()



    print('\\Starting Debiasing Mitigation...\\')
    with tqdm(range(int(3 * n_epochs/4))) as epochs:
        epochs.set_description(f"Adversarial Debiasing Training Epoch: dataset: {dataset_name}, seed {seed}, adversary_loss_weight:{alpha}")

        for epoch in epochs:
            adv.train()
            clf.train()
            for i, data in enumerate(train_loader):  # starting from the 0th batch
                x, y, z = data
                x = x.to(device)
                y = y.unsqueeze(1).to(device)
                z = z.unsqueeze(1).to(device)
                optimizer_clf.zero_grad()
                optimizer_adv.zero_grad()

                class_outputs = clf(x)
                class_loss = loss_clf(class_outputs, y)
                class_loss.backward(retain_graph=True)
                clf_grad = [torch.clone(par.grad.detach()) for par in clf.parameters()]



                domain_outputs = adv(class_outputs)
                optimizer_clf.zero_grad()
                optimizer_adv.zero_grad()




                domain_loss = loss_adv(domain_outputs, z)

                domain_loss.backward()

                ### Gradient of classifier
                adv_grad_clf = [
                    torch.clone(par.grad.detach()) for par in clf.parameters()
                ]

                ### Gradient of discriminator

                adv_grad_adv = [
                    torch.clone(par.grad.detach()) for par in adv.parameters()
                ]

                optimizer_clf.zero_grad()
                optimizer_adv.zero_grad()

                # Update the domain classifier
                for param, grad in zip(adv.parameters(), adv_grad_adv):
                    param.grad = grad
                optimizer_adv.step()


                optimizer_clf.zero_grad()
                optimizer_adv.zero_grad()
                #### adversarial debiasing
                for param, class_grad, domain_grad in zip(clf.parameters(), clf_grad,
                                                          adv_grad_clf):
                    param.grad = class_grad - alpha * domain_grad

                # Update the classifier

                optimizer_clf.step()
                epochs.set_postfix(lossCLF=class_loss.item(), lossAdv=domain_loss.item())

            lr_scheduler_clf.step()

            with torch.no_grad():


                output_val = clf(XZ_val.to(device))
                Yhat_val = (output_val>=0.5).squeeze().detach().cpu().numpy()
                accuracy=(Yhat_val==Y_val_np).mean()


                if epoch==0:
                    accuracy_max = accuracy
                    bestnet_acc_stat_dict_clf = clf.state_dict()


                if accuracy > accuracy_max:
                    accuracy_max = accuracy
                    bestnet_acc_stat_dict_clf = clf.state_dict()



    with torch.no_grad():
        clf.load_state_dict(bestnet_acc_stat_dict_clf)

        eta_test = clf(XZ_test).detach().cpu().numpy().squeeze()

        Y_test_np = Y_test.clone().detach().numpy()
        Z_test_np = Z_test.clone().detach().numpy()

        acc = cal_acc(eta_test, Y_test_np, Z_test_np, 0.5, 0.5)
        disparity = cal_disparity(eta_test, Z_test_np, 0.5, 0.5)
        data = [seed, dataset_name, alpha, acc, np.abs(disparity)]
        columns = ['seed', 'dataset', 'alpha', 'acc', 'disparity']
        df_test = pd.DataFrame([data], columns=columns)


    return df_test





def get_training_parameters(dataset_name):
    if dataset_name == 'AdultCensus':
        n_epochs = 200
        lr = 1e-1
        batch_size = 512


    return n_epochs,lr,batch_size





def training_ADV(dataset_name, alpha, seed):

    device = torch.device('cpu')


    # Set a seed for random number generation
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    # Import dataset
    dataset = FairnessDataset(dataset=dataset_name, seed=seed, device=device)
    dataset.normalize()
    input_dim = dataset.XZ_train.shape[1]
    n_epochs, lr, batch_size = get_training_parameters(dataset_name)
    # Create a classifier model


    print(f'we are runing {dataset_name} with seed: {seed} adversary_loss_weight: {alpha}')
    clf = Classifier(n_inputs=input_dim)
    adv = domain_Classifier()
    clf = clf.to(device)
    adv = adv.to(device)
    lr_decay = 0.98
    optimizer_clf = optim.Adam(clf.parameters(), lr=lr)
    optimizer_adv = optim.Adam(adv.parameters(), lr=0.01)
    lr_scheduler_clf = optim.lr_scheduler.ExponentialLR(optimizer_clf, gamma=lr_decay)
    # Set an optimizer


    Result = Adversarial(dataset=dataset,dataset_name=dataset_name,  alpha=alpha,
                       clf=clf, adv=adv,
                       optimizer_clf=optimizer_clf, optimizer_adv=optimizer_adv, lr_scheduler_clf=lr_scheduler_clf,
                       device=device, batch_size=batch_size, n_epochs=n_epochs, seed=seed)
    print(Result)
    Result.to_csv(f'Result/ADV/result_of_{dataset_name}_with_seed_{seed}_para_{int(alpha*1000)}')




