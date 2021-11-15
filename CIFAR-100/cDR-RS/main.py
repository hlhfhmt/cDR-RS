print("\n ===================================================================================================")

#----------------------------------------
import argparse
import os
import timeit
import torch
import torchvision
import torchvision.transforms as transforms
import numpy as np
import torch.nn as nn
import torch.backends.cudnn as cudnn
from torch.nn import functional as F
import random
import matplotlib.pyplot as plt
import matplotlib as mpl
mpl.use('Agg')
from torch import autograd
from torchvision.utils import save_image
from tqdm import tqdm, trange
import gc
from itertools import groupby
import multiprocessing
import h5py
import pickle
import copy
import shutil


#----------------------------------------
from opts import gen_synth_data_opts
from utils import *
from models import *
from train_cnn import train_cnn, test_cnn
from train_cdre import train_cdre
from eval_metrics import compute_FID, compute_IS



#######################################################################################
'''                                   Settings                                      '''
#######################################################################################
args = gen_synth_data_opts()
print(args)


if args.subsampling:
    subsampling_method = "cDRE-F-cSP_precnn_{}_lambda_{:.3f}_DR_{}_lambda_{:.3f}".format(args.dre_precnn_net, args.dre_precnn_lambda, args.dre_net, args.dre_lambda)
else:
    subsampling_method = "None"

path_torch_home = os.path.join(args.root_path, 'torch_cache')
os.makedirs(path_torch_home, exist_ok=True)
os.environ['TORCH_HOME'] = path_torch_home

#-------------------------------
# GAN and DRE
dre_precnn_lr_decay_epochs  = (args.dre_precnn_lr_decay_epochs).split("_")
dre_precnn_lr_decay_epochs = [int(epoch) for epoch in dre_precnn_lr_decay_epochs]

#-------------------------------
# seeds
random.seed(args.seed)
torch.manual_seed(args.seed)
torch.backends.cudnn.deterministic = True
cudnn.benchmark = False
np.random.seed(args.seed)

#-------------------------------
# output folders
precnn_models_directory = os.path.join(args.root_path, 'output/precnn_models')
os.makedirs(precnn_models_directory, exist_ok=True)

output_directory = os.path.join(args.root_path, 'output/Setting_{}'.format(args.gan_net))
os.makedirs(output_directory, exist_ok=True)

save_models_folder = os.path.join(output_directory, 'saved_models')
os.makedirs(save_models_folder, exist_ok=True)

save_traincurves_folder = os.path.join(output_directory, 'training_curves')
os.makedirs(save_traincurves_folder, exist_ok=True)

save_evalresults_folder = os.path.join(output_directory, 'eval_results')
os.makedirs(save_evalresults_folder, exist_ok=True)

dump_fake_images_folder = os.path.join(output_directory, 'dump_fake')
os.makedirs(dump_fake_images_folder, exist_ok=True)



#######################################################################################
'''                                  Load Data                                      '''
#######################################################################################
## generate subset
cifar_trainset = torchvision.datasets.CIFAR100(root = os.path.join(args.data_path, 'data'), train=True, download=True)
images_train = cifar_trainset.data
images_train = np.transpose(images_train, (0, 3, 1, 2))
labels_train = np.array(cifar_trainset.targets)

cifar_testset = torchvision.datasets.CIFAR100(root = os.path.join(args.data_path, 'data'), train=False, download=True)

### compute the mean and std for normalization
### Note that: In GAN-based KD, use computed mean and stds to normalize images for precnn training is better than using [0.5,0.5,0.5]
assert images_train.shape[1]==3
train_means = []
train_stds = []
for i in range(3):
    images_i = images_train[:,i,:,:]
    images_i = images_i/255.0
    train_means.append(np.mean(images_i))
    train_stds.append(np.std(images_i))
## for i
# train_means = [0.5,0.5,0.5]
# train_stds = [0.5,0.5,0.5]

images_test = cifar_testset.data
images_test = np.transpose(images_test, (0, 3, 1, 2))
labels_test = np.array(cifar_testset.targets)

print("\n Training set shape: {}x{}x{}x{}; Testing set shape: {}x{}x{}x{}.".format(images_train.shape[0], images_train.shape[1], images_train.shape[2], images_train.shape[3], images_test.shape[0], images_test.shape[1], images_test.shape[2], images_test.shape[3]))

''' transformations '''
if args.dre_precnn_transform:
    transform_precnn_train = transforms.Compose([
                transforms.RandomCrop((args.img_size, args.img_size), padding=4),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                transforms.Normalize(train_means, train_stds),
                ])
else:
    transform_precnn_train = transforms.Compose([
                transforms.ToTensor(),
                transforms.Normalize(train_means, train_stds),
                ])

if args.dre_transform:
    transform_dre = transforms.Compose([
                transforms.Resize(int(args.img_size*1.1)),
                transforms.RandomCrop(args.img_size),
                transforms.RandomHorizontalFlip(),
                transforms.Resize(args.img_size),
                transforms.ToTensor(),
                transforms.Normalize([0.5,0.5,0.5], [0.5,0.5,0.5]), ##do not use other normalization constants!!!
                ])
else:
    transform_dre = transforms.Compose([
                # transforms.RandomCrop((args.img_size, args.img_size), padding=4), ## note that some GAN training does not involve cropping!!!
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                transforms.Normalize([0.5,0.5,0.5], [0.5,0.5,0.5]), ##do not use other normalization constants!!!
                ])

# test set for cnn
transform_precnn_test = transforms.Compose([
                transforms.ToTensor(),
                transforms.Normalize(train_means, train_stds),
                ])
testset_precnn = IMGs_dataset(images_test, labels_test, transform=transform_precnn_test)
testloader_precnn = torch.utils.data.DataLoader(testset_precnn, batch_size=100, shuffle=False, num_workers=args.num_workers)



#######################################################################################
'''                  Load pre-trained GAN to Memory (not GPU)                       '''
#######################################################################################
ckpt_g = torch.load(args.gan_ckpt_path)
if args.gan_net=="BigGAN":
    netG = BigGAN_Generator(dim_z=args.gan_dim_g, resolution=args.img_size, G_attn='0', n_classes=args.num_classes, G_shared=False)
    netG.load_state_dict(ckpt_g)
    netG = nn.DataParallel(netG)
elif args.gan_net=="SNGAN":
    netG = SNGAN_Generator(dim_z=args.gan_dim_g, num_classes=args.num_classes)
    netG.load_state_dict(ckpt_g['netG_state_dict'])
    netG = nn.DataParallel(netG)
elif args.gan_net=="ACGAN":
    netG = ACGAN_Generator(nz=args.gan_dim_g, ny=args.num_classes)
    netG.load_state_dict(ckpt_g['netG_state_dict'])
    netG = nn.DataParallel(netG)
else:
    raise Exception("Not supported GAN!!")

def fn_sampleGAN_given_label(nfake, given_label, batch_size, pretrained_netG=netG, to_numpy=True):
    raw_fake_images = []
    raw_fake_labels = []
    pretrained_netG = pretrained_netG.cuda()
    pretrained_netG.eval()
    with torch.no_grad():
        tmp = 0
        while tmp < nfake:
            z = torch.randn(batch_size, args.gan_dim_g, dtype=torch.float).cuda()
            labels = (given_label*torch.ones(batch_size)).type(torch.long).cuda()
            batch_fake_images = pretrained_netG(z, labels)
            raw_fake_images.append(batch_fake_images.cpu())
            raw_fake_labels.append(labels.cpu().view(-1))
            tmp += batch_size

    raw_fake_images = torch.cat(raw_fake_images, dim=0)
    raw_fake_labels = torch.cat(raw_fake_labels)

    if to_numpy:
        raw_fake_images = raw_fake_images.numpy()
        raw_fake_labels = raw_fake_labels.numpy()

    return raw_fake_images[0:nfake], raw_fake_labels[0:nfake]



#######################################################################################
'''                                  DRE Training                                   '''
#######################################################################################
if args.subsampling:
    ##############################################
    ''' Pre-trained CNN for feature extraction '''
    print("\n -----------------------------------------------------------------------------------------")
    print("\n Pre-trained CNN for feature extraction")
    # data loader
    trainset_dre_precnn = IMGs_dataset(images_train, labels_train, transform=transform_precnn_train)
    trainloader_dre_precnn = torch.utils.data.DataLoader(trainset_dre_precnn, batch_size=args.dre_precnn_batch_size_train, shuffle=True, num_workers=args.num_workers)
    # Filename
    filename_precnn_ckpt = precnn_models_directory + '/ckpt_PreCNNForDRE_{}_lambda_{}_epoch_{}_transform_{}_ntrain_{}_seed_{}.pth'.format(args.dre_precnn_net, args.dre_precnn_lambda, args.dre_precnn_epochs, args.dre_precnn_transform, args.ntrain, args.seed)
    print('\n' + filename_precnn_ckpt)

    path_to_ckpt_in_train = precnn_models_directory + '/ckpts_in_train_PreCNNForDRE_{}_lambda_{}_ntrain_{}_seed_{}'.format(args.dre_precnn_net, args.dre_precnn_lambda, args.ntrain, args.seed)
    os.makedirs(path_to_ckpt_in_train, exist_ok=True)

    # initialize cnn
    dre_precnn_net = cnn_extract_initialization(args.dre_precnn_net, num_classes=args.num_classes)
    num_parameters = count_parameters(dre_precnn_net)
    if args.dre_precnn_lambda>0:
        dre_precnn_decoder_net = mobilenet_decoder()
    else:
        dre_precnn_decoder_net = None
    # training
    if not os.path.isfile(filename_precnn_ckpt):
        print("\n Start training CNN for feature extraction in the DRE >>>")
        dre_precnn_net = train_cnn(dre_precnn_net, 'PreCNNForDRE_{}'.format(args.dre_precnn_net), trainloader_dre_precnn, testloader_precnn, epochs=args.dre_precnn_epochs, resume_epoch=args.dre_precnn_resume_epoch, lr_base=args.dre_precnn_lr_base, lr_decay_factor=args.dre_precnn_lr_decay_factor, lr_decay_epochs=dre_precnn_lr_decay_epochs, weight_decay=args.dre_precnn_weight_decay, extract_feature=True, net_decoder=dre_precnn_decoder_net, lambda_reconst=args.dre_precnn_lambda, train_means=train_means, train_stds=train_stds, path_to_ckpt = path_to_ckpt_in_train)

        # store model
        torch.save({
            'net_state_dict': dre_precnn_net.state_dict(),
        }, filename_precnn_ckpt)
        print("\n End training CNN.")
    else:
        print("\n Loading pre-trained CNN for feature extraction in DRE.")
        checkpoint = torch.load(filename_precnn_ckpt)
        dre_precnn_net.load_state_dict(checkpoint['net_state_dict'])
    #end if

    # testing
    _ = test_cnn(dre_precnn_net, testloader_precnn, extract_feature=True, verbose=True)



    ##############################################
    ''' cDRE Training '''
    print("\n -----------------------------------------------------------------------------------------")
    print("\n cDRE training")

    ### dataloader
    trainset_dre = IMGs_dataset(images_train, labels_train, transform=transform_dre)
    trainloader_dre = torch.utils.data.DataLoader(trainset_dre, batch_size=args.dre_batch_size, shuffle=True, num_workers=args.num_workers)

    ### dr model filename
    drefile_fullpath = save_models_folder + "/ckpt_cDRE-F-cSP_precnn_{}_lambda_{:.3f}_DR_{}_lambda_{:.3f}_epochs_{}_ntrain_{}_seed_{}.pth".format(args.dre_precnn_net, args.dre_precnn_lambda, args.dre_net, args.dre_lambda, args.dre_epochs, args.ntrain, args.seed)
    print('\n' + drefile_fullpath)

    path_to_ckpt_in_train = save_models_folder + '/ckpt_cDRE-F-cSP_precnn_{}_lambda_{:.3f}_DR_{}_lambda_{:.3f}_ntrain_{}_seed_{}'.format(args.dre_precnn_net, args.dre_precnn_lambda, args.dre_net, args.dre_lambda, args.ntrain, args.seed)
    os.makedirs(path_to_ckpt_in_train, exist_ok=True)

    dre_loss_file_fullpath = save_traincurves_folder + '/train_loss_cDRE-F-cSP_precnn_{}_lambda_{:.3f}_DR_{}_epochs_{}_lambda_{}_ntrain_{}_seed_{}.png'.format(args.dre_precnn_net, args.dre_precnn_lambda, args.dre_net, args.dre_epochs, args.dre_lambda, args.ntrain, args.seed)

    ### dre training
    dre_net = cDR_MLP(args.dre_net, p_dropout=0.5, init_in_dim = args.num_channels*args.img_size*args.img_size, num_classes = args.num_classes).cuda()
    num_parameters_DR = count_parameters(dre_net)
    dre_net = nn.DataParallel(dre_net)
    #if DR model exists, then load the pretrained model; otherwise, start training the model.
    if not os.path.isfile(drefile_fullpath):
        print("\n Begin Training conditional DR in Feature Space: >>>")
        dre_net, avg_train_loss = train_cdre(trainloader_dre, dre_net, dre_precnn_net, netG, path_to_ckpt=path_to_ckpt_in_train)

        # save model
        torch.save({
        'net_state_dict': dre_net.state_dict(),
        }, drefile_fullpath)
        PlotLoss(avg_train_loss, dre_loss_file_fullpath)
    else:
        # if already trained, load pre-trained DR model
        checkpoint_dre_net = torch.load(drefile_fullpath)
        dre_net.load_state_dict(checkpoint_dre_net['net_state_dict'])
    ##end if not


    # Compute density ratio: function for computing a bunch of images in a numpy array
    def comp_cond_density_ratio(imgs, labels, batch_size=args.samp_batch_size):
        #imgs: a torch tensor
        n_imgs = len(imgs)
        if batch_size>n_imgs:
            batch_size = n_imgs

        ##make sure the last iteration has enough samples
        imgs = torch.cat((imgs, imgs[0:batch_size]), dim=0)
        labels = torch.cat((labels, labels[0:batch_size]), dim=0)

        density_ratios = []
        dre_net.eval()
        dre_precnn_net.eval()
        # print("\n Begin computing density ratio for images >>")
        with torch.no_grad():
            n_imgs_got = 0
            while n_imgs_got < n_imgs:
                batch_images = imgs[n_imgs_got:(n_imgs_got+batch_size)]
                batch_labels = labels[n_imgs_got:(n_imgs_got+batch_size)]
                batch_images = batch_images.type(torch.float).cuda()
                batch_labels = batch_labels.type(torch.long).cuda()
                _, batch_features = dre_precnn_net(batch_images)
                batch_ratios = dre_net(batch_features, batch_labels)
                density_ratios.append(batch_ratios.cpu().detach())
                n_imgs_got += batch_size
            ### while n_imgs_got
        density_ratios = torch.cat(density_ratios)
        density_ratios = density_ratios[0:n_imgs].numpy()
        return density_ratios


    # Enhanced sampler based on the trained DR model
    # Rejection Sampling:"Discriminator Rejection Sampling"; based on https://github.com/shinseung428/DRS_Tensorflow/blob/master/config.py
    def fn_enhancedSampler_given_label(nfake, given_label, batch_size=args.samp_batch_size, verbose=True):
        ## Burn-in Stage
        n_burnin = args.samp_burnin_size
        burnin_imgs, burnin_labels = fn_sampleGAN_given_label(n_burnin, given_label, batch_size, to_numpy=False)
        burnin_densityratios = comp_cond_density_ratio(burnin_imgs, burnin_labels)
        print((burnin_densityratios.min(),np.median(burnin_densityratios),burnin_densityratios.max()))
        M_bar = np.max(burnin_densityratios)
        del burnin_imgs, burnin_densityratios; gc.collect()
        ## Rejection sampling
        enhanced_imgs = []
        if verbose:
            pb = SimpleProgressBar()
            # pbar = tqdm(total=nfake)
        num_imgs = 0
        while num_imgs < nfake:
            batch_imgs, batch_labels = fn_sampleGAN_given_label(batch_size, given_label, batch_size, to_numpy=False)
            batch_ratios = comp_cond_density_ratio(batch_imgs, batch_labels)
            M_bar = np.max([M_bar, np.max(batch_ratios)])
            #threshold
            batch_p = batch_ratios/M_bar
            batch_psi = np.random.uniform(size=batch_size).reshape(-1,1)
            indx_accept = np.where(batch_psi<=batch_p)[0]
            if len(indx_accept)>0:
                enhanced_imgs.append(batch_imgs[indx_accept])
            num_imgs+=len(indx_accept)
            del batch_imgs, batch_ratios; gc.collect()
            if verbose:
                pb.update(np.min([float(num_imgs)*100/nfake,100]))
                # pbar.update(len(indx_accept))
        # pbar.close()
        enhanced_imgs = np.concatenate(enhanced_imgs, axis=0)
        enhanced_imgs = enhanced_imgs[0:nfake]
        return enhanced_imgs, given_label*np.ones(nfake)
    


###############################################################################
'''                             Compute FID and IS                          '''
###############################################################################
if args.eval or args.samp_dump_fake_data:
    if args.inception_from_scratch:
        #load pre-trained InceptionV3 (pretrained on CIFAR-100)
        PreNetFID = Inception3(num_classes=args.num_classes, aux_logits=True, transform_input=False)
        checkpoint_PreNet = torch.load(args.eval_ckpt_path)
        PreNetFID = nn.DataParallel(PreNetFID).cuda()
        PreNetFID.load_state_dict(checkpoint_PreNet['net_state_dict'])
    else:
        PreNetFID = inception_v3(pretrained=True, transform_input=True)
        PreNetFID = nn.DataParallel(PreNetFID).cuda()
    

    ##############################################
    ''' Compute FID between real and fake images '''
    IS_scores_all = []
    FID_scores_all = []
    Intra_FID_scores_all = []

    start = timeit.default_timer()
    for nround in range(args.samp_round):
        print("\n {}+{}, Eval round: {}/{}".format(args.gan_net, subsampling_method, nround+1, args.samp_round))

        ### generate fake images
        dump_fake_images_filename = os.path.join(dump_fake_images_folder, 'fake_images_{}_subsampling_{}_NfakePerClass_{}_seed_{}_Round_{}_of_{}.h5'.format(args.gan_net, subsampling_method, args.samp_nfake_per_class, args.seed, nround+1, args.samp_round))

        if not os.path.isfile(dump_fake_images_filename):
            print('\n Start generating fake data...')
            fake_images = []
            fake_labels = []
            for i in range(args.num_classes):
                print("\n Generate {} fake images for class {}/{}.".format(args.samp_nfake_per_class, i+1, args.num_classes))
                if args.subsampling:
                    fake_images_i, fake_labels_i = fn_enhancedSampler_given_label(nfake=args.samp_nfake_per_class, given_label=i, batch_size=args.samp_batch_size, verbose=True)
                else:
                    fake_images_i, fake_labels_i = fn_sampleGAN_given_label(nfake=args.samp_nfake_per_class, given_label=i, batch_size=args.samp_batch_size, pretrained_netG=netG, to_numpy=True)
                assert fake_images_i.max()<=1 and fake_images_i.min()>=-1
                ## denormalize images to save memory
                fake_images_i = (fake_images_i*0.5+0.5)*255.0
                fake_images_i = fake_images_i.astype(np.uint8)
                assert fake_images_i.max()>1 and fake_images_i.max()<=255.0
                fake_images.append(fake_images_i)
                fake_labels.append(fake_labels_i.reshape(-1))
            fake_images = np.concatenate(fake_images, axis=0)
            fake_labels = np.concatenate(fake_labels, axis=0)
            del fake_images_i, fake_labels_i; gc.collect()
            print('\n End generating fake data!')

            if args.samp_dump_fake_data:
                with h5py.File(dump_fake_images_filename, "w") as f:
                    f.create_dataset('fake_images', data = fake_images, dtype='uint8', compression="gzip", compression_opts=6)
                    f.create_dataset('fake_labels', data = fake_labels, dtype='float')
        else:
            print('\n Start loading generated fake data...')
            with h5py.File(dump_fake_images_filename, "r") as f:
                fake_images = f['fake_images'][:]
                fake_labels = f['fake_labels'][:]
        assert len(fake_images) == len(fake_labels)



        # ### generate fake images
        # dump_fake_images_folder_nround = os.path.join(dump_fake_images_folder, 'fake_images_{}_subsampling_{}_NfakePerClass_{}_seed_{}_Round_{}_of_{}'.format(args.gan_net, subsampling_method, args.samp_nfake_per_class, args.seed, nround+1, args.samp_round))
        # os.makedirs(dump_fake_images_folder_nround, exist_ok=True)

        # fake_images = []
        # fake_labels = []
        # for i in range(args.num_classes):
        #     dump_fake_images_filename = os.path.join(dump_fake_images_folder_nround, 'class_{}_of_{}.h5'.format(i+1,args.num_classes))

        #     if not os.path.isfile(dump_fake_images_filename):
        #         print("\n Start generating {} fake images for class {}/{}.".format(args.samp_nfake_per_class, i+1, args.num_classes))
        #         if args.subsampling:
        #             fake_images_i, fake_labels_i = fn_enhancedSampler_given_label(nfake=args.samp_nfake_per_class, given_label=i, batch_size=args.samp_batch_size, verbose=True)
        #         else:
        #             fake_images_i, fake_labels_i = fn_sampleGAN_given_label(nfake=args.samp_nfake_per_class, given_label=i, batch_size=args.samp_batch_size, pretrained_netG=netG, to_numpy=True)
        #         assert fake_images_i.max()<=1 and fake_images_i.min()>=-1
        #         ## denormalize images to save memory
        #         fake_images_i = (fake_images_i*0.5+0.5)*255.0
        #         fake_images_i = fake_images_i.astype(np.uint8)

        #         if args.samp_dump_fake_data:
        #             with h5py.File(dump_fake_images_filename, "w") as f:
        #                 f.create_dataset('fake_images_i', data = fake_images_i, dtype='uint8', compression="gzip", compression_opts=6)
        #                 f.create_dataset('fake_labels_i', data = fake_labels_i, dtype='float')

        #     else:
        #         print('\n Start loading generated fake data for class {}/{}...'.format(i+1,args.num_classes))
        #         with h5py.File(dump_fake_images_filename, "r") as f:
        #             fake_images_i = f['fake_images_i'][:]
        #             fake_labels_i = f['fake_labels_i'][:]
            
        #     assert fake_images_i.max()>1 and fake_images_i.max()<=255.0
        #     fake_images.append(fake_images_i)
        #     fake_labels.append(fake_labels_i.reshape(-1))
        # ##end for i
        # fake_images = np.concatenate(fake_images, axis=0)
        # fake_labels = np.concatenate(fake_labels)



        ## normalize images
        assert fake_images.max()>1
        fake_images = (fake_images/255.0-0.5)/0.5
        assert images_train.max()>1
        images_train = (images_train/255.0-0.5)/0.5
        assert -1.0<=images_train.max()<=1.0 and -1.0<=images_train.min()<=1.0

        if args.eval:
            #####################
            ## Compute Intra-FID: real vs fake
            print("\n Start compute Intra-FID between real and fake images...")
            start_time = timeit.default_timer()
            intra_fid_scores = np.zeros(args.num_classes)
            for i in range(args.num_classes):
                indx_train_i = np.where(labels_train==i)[0]
                images_train_i = images_train[indx_train_i]
                indx_fake_i = np.where(fake_labels==i)[0]
                fake_images_i = fake_images[indx_fake_i]
                ##compute FID within each class
                intra_fid_scores[i] = compute_FID(PreNetFID, images_train_i, fake_images_i, batch_size = args.eval_FID_batch_size, resize = (299, 299))
                print("\r Eval round: {}/{}; Class:{}; Real:{}; Fake:{}; FID:{}; Time elapses:{}s.".format(nround+1, args.samp_round, i+1, len(images_train_i), len(fake_images_i), intra_fid_scores[i], timeit.default_timer()-start_time))
            ##end for i
            # average over all classes
            print("\n Eval round: {}/{}; Intra-FID: {}({}); min/max: {}/{}.".format(nround+1, args.samp_round, np.mean(intra_fid_scores), np.std(intra_fid_scores), np.min(intra_fid_scores), np.max(intra_fid_scores)))

            # dump FID versus class to npy
            dump_fids_filename = save_evalresults_folder + "/{}_subsampling_{}_round_{}_of_{}_fids_scratchInceptionNet_{}".format(args.gan_net, subsampling_method, nround+1, args.samp_round, args.inception_from_scratch)
            np.savez(dump_fids_filename, fids=intra_fid_scores)

            #####################
            ## Compute FID: real vs fake
            print("\n Start compute FID between real and fake images...")
            indx_shuffle_real = np.arange(len(images_train)); np.random.shuffle(indx_shuffle_real)
            indx_shuffle_fake = np.arange(len(fake_images)); np.random.shuffle(indx_shuffle_fake)
            fid_score = compute_FID(PreNetFID, images_train[indx_shuffle_real], fake_images[indx_shuffle_fake], batch_size = args.eval_FID_batch_size, resize = (299, 299))
            print("\n Eval round: {}/{}; FID between {} real and {} fake images: {}.".format(nround+1, args.samp_round, len(images_train), len(fake_images), fid_score))
            
            #####################
            ## Compute IS
            print("\n Start compute IS of fake images...")
            indx_shuffle_fake = np.arange(len(fake_images)); np.random.shuffle(indx_shuffle_fake)
            is_score, is_score_std = compute_IS(PreNetFID, fake_images[indx_shuffle_fake], batch_size = args.eval_FID_batch_size, splits=10, resize=(299,299))
            print("\n Eval round: {}/{}; IS of {} fake images: {}({}).".format(nround+1, args.samp_round, len(fake_images), is_score, is_score_std))

            #####################
            # Dump evaluation results
            eval_results_fullpath = os.path.join(save_evalresults_folder, '{}_subsampling_{}_scratchInceptionNet_{}.txt'.format(args.gan_net, subsampling_method, args.inception_from_scratch))
            if not os.path.isfile(eval_results_fullpath):
                eval_results_logging_file = open(eval_results_fullpath, "w")
                eval_results_logging_file.close()
            with open(eval_results_fullpath, 'a') as eval_results_logging_file:
                eval_results_logging_file.write("\n===================================================================================================")
                eval_results_logging_file.write("\n Separate results for {} of {} rounds; Subsampling {} \n".format(nround, args.samp_round, subsampling_method))
                print(args, file=eval_results_logging_file)
                eval_results_logging_file.write("\n Intra-FID: {}({}); min/max: {}/{}.".format(np.mean(intra_fid_scores), np.std(intra_fid_scores), np.min(intra_fid_scores), np.max(intra_fid_scores)))
                eval_results_logging_file.write("\n FID: {}.".format(fid_score))
                eval_results_logging_file.write("\n IS: {}({}).".format(is_score, is_score_std))

            ## store
            FID_scores_all.append(fid_score)
            Intra_FID_scores_all.append(np.mean(intra_fid_scores))
            IS_scores_all.append(is_score)
        ##end if args.eval
    ##end nround
    stop = timeit.default_timer()
    print("Sampling and evaluation finished! Time elapses: {}s".format(stop - start))
        
    if args.eval:

        FID_scores_all = np.array(FID_scores_all)
        Intra_FID_scores_all = np.array(Intra_FID_scores_all)
        IS_scores_all = np.array(IS_scores_all)

        #####################
        # Average Eval results
        print("\n Avg Intra-FID over {} rounds: {}({}); min/max: {}/{}.".format(args.samp_round, np.mean(Intra_FID_scores_all), np.std(Intra_FID_scores_all), np.min(Intra_FID_scores_all), np.max(Intra_FID_scores_all)))

        print("\n Avg FID over {} rounds: {}({}); min/max: {}/{}.".format(args.samp_round, np.mean(FID_scores_all), np.std(FID_scores_all), np.min(FID_scores_all), np.max(FID_scores_all)))

        print("\n Avg IS over {} rounds: {}({}); min/max: {}/{}.".format(args.samp_round, np.mean(IS_scores_all), np.std(IS_scores_all), np.min(IS_scores_all), np.max(IS_scores_all)))
        
        #####################
        # Dump evaluation results
        eval_results_fullpath = os.path.join(save_evalresults_folder, '{}_subsampling_{}_scratchInceptionNet_{}.txt'.format(args.gan_net, subsampling_method, args.inception_from_scratch))
        if not os.path.isfile(eval_results_fullpath):
            eval_results_logging_file = open(eval_results_fullpath, "w")
            eval_results_logging_file.close()
        with open(eval_results_fullpath, 'a') as eval_results_logging_file:
            eval_results_logging_file.write("\n===================================================================================================")
            eval_results_logging_file.write("\n Average results over {} rounds; Subsampling {} \n".format(args.samp_round, subsampling_method))
            print(args, file=eval_results_logging_file)
            eval_results_logging_file.write("\n Avg. Intra-FID over {} rounds: {}({}); min/max: {}/{}.".format(args.samp_round, np.mean(Intra_FID_scores_all), np.std(Intra_FID_scores_all), np.min(Intra_FID_scores_all), np.max(Intra_FID_scores_all)))
            eval_results_logging_file.write("\n Avg. FID over {} rounds: {}({}); min/max: {}/{}.".format(args.samp_round, np.mean(FID_scores_all), np.std(FID_scores_all), np.min(FID_scores_all), np.max(FID_scores_all)))
            eval_results_logging_file.write("\n Avg. IS over {} rounds: {}({}); min/max: {}/{}.".format(args.samp_round, np.mean(IS_scores_all), np.std(IS_scores_all), np.min(IS_scores_all), np.max(IS_scores_all)))
    ## if args.eval
    


#######################################################################################
'''               Visualize fake images of the trained GAN                          '''
#######################################################################################
if args.visualize_fake_images:
    
    # First, visualize conditional generation # vertical grid
    ## 10 rows; 10 columns (10 samples for each class)
    n_row = args.num_classes
    n_col = 10

    fake_images_view = []
    fake_labels_view = []
    for i in range(args.num_classes):
        fake_labels_i = i*np.ones(n_col)
        if args.subsampling:
            fake_images_i, _ = fn_enhancedSampler_given_label(nfake=n_col, given_label=i, batch_size=100, verbose=False)
        else:
            fake_images_i, _ = fn_sampleGAN_given_label(nfake=n_col, given_label=i, batch_size=100, pretrained_netG=netG, to_numpy=True)
        fake_images_view.append(fake_images_i)
        fake_labels_view.append(fake_labels_i)
    ##end for i
    fake_images_view = np.concatenate(fake_images_view, axis=0)
    fake_labels_view = np.concatenate(fake_labels_view, axis=0)

    ### output fake images from a trained GAN
    filename_fake_images = save_evalresults_folder + '/{}_subsampling_{}_fake_image_grid_{}x{}.png'.format(args.gan_net, subsampling_method, n_row, n_col)
    
    images_show = np.zeros((n_row*n_col, args.num_channels, args.img_size, args.img_size))
    for i_row in range(n_row):
        indx_i = np.where(fake_labels_view==i_row)[0]
        for j_col in range(n_col):
            curr_image = fake_images_view[indx_i[j_col]]
            images_show[i_row*n_col+j_col,:,:,:] = curr_image
    images_show = torch.from_numpy(images_show)
    save_image(images_show.data, filename_fake_images, nrow=n_col, normalize=True)

### end if args.visualize_fake_images


print("\n ===================================================================================================")